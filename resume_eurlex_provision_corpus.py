"""Resume the existing EUR-Lex run with unique labels for colliding targets."""

from collections import defaultdict
from pathlib import Path
import runpy
import subprocess
import sys

from eurlex_provision import evidence as evidence_module


ROOT = Path(__file__).resolve().parent
CONTEXT_LIMIT = "The evidence pack exceeds the model context limit."
SECTION_CONTEXT_LIMIT = "Annex section exceeds the context limit:"


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


def skip_attempted_batch(summary, expected):
    """Continue past fully attempted batches whose remaining requests need smaller packs."""
    if not summary or summary.get("error"):
        return False
    exported = summary.get("targets_exported", 0)
    failed = summary.get("targets_failed", 0)
    if exported == expected and not failed:
        return True
    failures = summary.get("failures", [])
    if not failed or exported + failed != expected or len(failures) != failed:
        return False
    oversized = all(
        failure.get("error", "") == CONTEXT_LIMIT
        or failure.get("error", "").startswith(SECTION_CONTEXT_LIMIT)
        for failure in failures
    )
    if oversized:
        print(f"Deferred {failed} context-limit failures from a fully attempted batch.", flush=True)
    return oversized


def main():
    if sys.argv[1:2] == ["--worker"]:
        del sys.argv[1]
        # Apply the locator fix only inside this worker. The existing runner,
        # plan files and checkpoint validation keep their original inputs.
        evidence_module.Evidence = UniqueTargetEvidence
        runpy.run_path(str(ROOT / "screen_eurlex_provisions_simple.py"), run_name="__main__")
        return 0

    import run_eurlex_provision_corpus as launcher

    launcher.run_batch = run_batch
    launcher.complete = skip_attempted_batch
    return launcher.main()


if __name__ == "__main__":
    raise SystemExit(main())
