"""Resume EUR-Lex screening with unique labels and bounded context recovery."""

from collections import defaultdict
from functools import partial
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys

from eurlex_provision import evidence as evidence_module


ROOT = Path(__file__).resolve().parent
TARGET_CHARS = 14000
MAX_RECOVERY_REQUESTS = 128
SUPPORT_BUDGETS = {
    "essential_context": 4000, "direct_references": 5000,
    "footnotes": 2000, "unavailable_references": 1000,
}


class UniqueTargetEvidence(evidence_module.Evidence):
    """Keep existing labels unless two screening units would export to the same key."""

    def __init__(self, data, supplements):
        self.unique_locators = {}
        super().__init__(data, supplements)

        targets_by_label = defaultdict(list)
        for key, unit in self.units.items():
            if unit["kind"] in evidence_module.TARGET_KINDS:
                targets_by_label[self.locator(key)].append(key)

        occupied = set(targets_by_label)
        for label, keys in targets_by_label.items():
            if len(keys) == 1:
                continue
            for key in sorted(keys):
                unique = f"{label} [source ID {key}]"
                while unique in occupied:
                    unique += " [duplicate]"
                occupied.add(unique)
                self.unique_locators[key] = unique
                self.aliases[evidence_module.norm(unique)].add(key)
                if key in self.tables:
                    for row in self.tables[key]["rows"]:
                        alias = self.row_locator(key, row["row"])
                        self.aliases[evidence_module.norm(alias)].add((key, row["row"]))

        if self.unique_locators:
            print(f"{self.document['celex']}: disambiguated {len(self.unique_locators)} target labels.",
                  flush=True)

    def locator(self, key):
        if key in self.unique_locators:
            return self.unique_locators[key]
        return super().locator(key)


def run_batch(args, directory, manifest, prepare_only=False):
    from enrich_eurlex import write_json

    manifest_path = directory / "manifest.json"
    write_json(manifest_path, manifest)
    command = [
        sys.executable, str(Path(__file__).resolve()), "--worker", "pilot",
        "--manifest", str(manifest_path), "--enriched-dir", str(args.enriched_dir),
        "--anchors", str(args.anchors), "--env-file", str(args.env_file),
        "--output-dir", str(directory),
    ]
    if prepare_only:
        command.append("--prepare-only")
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def size(value):
    return len(json.dumps(value, ensure_ascii=False))


def words(text):
    return set(re.findall(r"\b[a-zA-ZÀ-ÿ]{5,}\b", text.lower()))


def context_excerpt(entry, terms, limit, target_text):
    text = "\n".join(target_text(entry))
    if not text:
        return None
    locator = str(entry.get("locator", entry.get("source_id", "")))[:200]
    # Keep the opening scope and a passage matching the current target.
    window = max(120, (limit - len(locator) - 120) // 4)
    candidates = (text[start:start + window] for start in range(window, len(text), window))
    best = max(candidates, key=lambda part: len(terms & words(part)), default="")
    excerpt = text[:window] + (f"\n[excerpt]\n{best}" if best and terms & words(best) else "")
    return {"locator": locator, "text": excerpt, "coverage": "Excerpt of supporting context"}


def compact_support(pack, provision, target_text, scale):
    terms = words(" ".join(target_text(provision))) | words(str(provision.get("locator", "")))
    reduced = {**pack, "provision": provision}
    changes = {}
    for field, budget in SUPPORT_BUDGETS.items():
        original = pack.get(field, [])
        remaining = int(budget * scale)
        if size(original) <= remaining:
            continue
        entries = []
        # Reserve room for multiple sources when one annex or reference is large.
        share = max(550, remaining // min(len(original), 4))
        for entry in original:
            limit = min(remaining, share)
            if limit < 300:
                break
            item = entry if size(entry) <= limit else context_excerpt(entry, terms, limit, target_text)
            if item is not None and size(item) + 2 <= remaining:
                entries.append(item)
                remaining -= size(item) + 2
        reduced[field] = entries
        changes[field] = {"sources_original": len(original), "sources_sent": len(entries),
                          "excerpts_or_omissions": True}
    return reduced, changes


def split_provision(node):
    """Split a large target without dropping its text, rows or section blocks."""
    for field in ("sections", "blocks", "rows"):
        values = node.get(field)
        if not values:
            continue
        if len(values) > 1:
            middle = len(values) // 2
            headers = []
            if field == "rows":
                for row in values:
                    if not row or not all(cell.get("header") for cell in row):
                        break
                    headers.append(row)
                # Split only between rows that are not connected by a row span.
                blocked = {cut for i, row in enumerate(values) for cell in row
                           for cut in range(i + 1, min(len(values), i + cell.get("rowspan", 1)))}
                valid = [cut for cut in range(len(headers) + 1, len(values)) if cut not in blocked]
                if not valid:
                    return ()
                middle = min(valid, key=lambda cut: (abs(cut - middle), cut))
            return ({**node, field: values[:middle]}, {**node, field: headers + values[middle:]})
        if isinstance(values[0], dict):
            return tuple({**node, field: [part]} for part in split_provision(values[0]))
    if "table" in node:
        return tuple({**node, "table": part} for part in split_provision(node["table"]))
    text = node.get("text", "")
    if len(text) < 1000:
        return ()
    middle = len(text) // 2
    boundaries = [match.end() for match in re.finditer(r"[.!?]\s+|\n+", text)
                  if len(text) // 3 <= match.end() <= len(text) * 2 // 3]
    cut = min(boundaries, key=lambda end: abs(end - middle)) if boundaries else middle
    overlap = min(180, len(text) // 12)
    return ({**node, "text": text[:cut + overlap]}, {**node, "text": text[cut - overlap:]})


def score_with_context_recovery(simple, original_score, client, prompt, pack):
    try:
        return original_score(client, prompt, pack)
    except simple.ContextLimitError as original:
        failure = str(original)

    print(f"Recovering oversized evidence for {pack['provision']['locator']}.", flush=True)
    instruction = (
        f"{prompt}\n\nContext recovery: assess the supplied target text or rows. "
        "The target may be sent in parts. Supporting sources may be excerpts. "
        "Keep conditions and exceptions in scope, and never infer company facts or obligations "
        "from omitted context."
    )
    completed = []
    requests = 0

    def screen(node, part_id):
        nonlocal requests
        if size(node) > TARGET_CHARS and (children := split_provision(node)):
            for index, child in enumerate(children, 1):
                screen(child, f"{part_id}.{index}")
            return
        for scale in (1.0, 0.45):
            if requests >= MAX_RECOVERY_REQUESTS:
                raise simple.TargetScreeningError("Context recovery reached its request limit.", completed)
            reduced, changes = compact_support(pack, node, simple.target_text, scale)
            requests += 1
            try:
                decision, audit = simple.score_request(client, instruction, reduced)
            except simple.ContextLimitError:
                continue
            completed.append({"part": part_id, "decision": decision, "audit": audit,
                              "supporting_context": changes, "target_chars": size(node)})
            return
        children = split_provision(node)
        if not children:
            raise simple.TargetScreeningError(
                f"Context recovery could not fit the smallest safe target part {part_id}.", completed)
        for index, child in enumerate(children, 1):
            screen(child, f"{part_id}.{index}")

    screen(pack["provision"], "1")
    winner = max(completed, key=lambda item: simple.LEVEL_PRIORITY[item["decision"]["level"]])
    decision = winner["decision"]
    if len(completed) > 1:
        decision = {**decision, "reason": f"Screened all {len(completed)} target parts. {decision['reason']}"[:600]}
    return decision, {**winner["audit"], "context_recovery": {
        "original_error": failure, "requests": requests, "parts": completed,
    }}


def main():
    if sys.argv[1:2] == ["--worker"]:
        del sys.argv[1]
        from eurlex_provision import simple

        # Apply the fixes inside the worker and retain the existing plan,
        # checkpoint fingerprints and full original evidence in the audit.
        evidence_module.Evidence = UniqueTargetEvidence
        simple.score = partial(score_with_context_recovery, simple, simple.score)
        runpy.run_path(str(ROOT / "screen_eurlex_provisions_simple.py"), run_name="__main__")
        return 0

    import run_eurlex_provision_corpus as launcher

    launcher.run_batch = run_batch
    return launcher.main()


if __name__ == "__main__":
    raise SystemExit(main())
