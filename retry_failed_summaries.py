"""Retry recorded provision failures in an existing summary run."""
import argparse
from collections import Counter, defaultdict
import os
import tempfile
import time
from datetime import datetime, timezone
from summarize_eurlex_provisions import ACT_SCHEMA, department_schema, provision_facts
import html
import inspect
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from classify_eurlex_provisions import (  # noqa: E402
    DEFAULT_RUNS, DEFAULT_UNSURE, KEPT, MAX_WORKERS, MIN_PART_CHARS, NO_DEPARTMENT, OPENING_CHARS, TARGET_CHARS,
    ConnectionLost, Unclassified, act_links, collect, reachable, read_json, record_path, request_pack,
    run_until_done, safe_name, sample, sha, split_target, squash, target_text, wait_until_reachable, write_json)
from eurlex_retry_checks import Source, number_problems, states_nothing, wrong_language  # noqa: E402
from lr_ai.qwen_client import QwenClient  # noqa: E402

PROMPTS = {name: ROOT / f"prompts/eurlex_{name}_summary.md" for name in ("provision", "merge", "act", "department")}
DEFAULT_CLASSIFICATION = ROOT / "runs/eurlex_classification_v5"
DEFAULT_OUTPUT = ROOT / "runs/eurlex_summaries_v1"
TASKS = {
    "provision": "TASK: Summarise this provision. Return the JSON object described in the instructions.",
    "merge": "TASK: Write one headline and one overview for the whole provision. Return the JSON object described "
             "in the instructions.",
    "act": "TASK: Write the act summary. Return the JSON object described in the instructions.",
    "department": "TASK: Write the department summary. Return the JSON object described in the instructions.",
}
LANGUAGES = {"de": "German", "en": "English"}
OUTPUT_LANGUAGE = None   # set by main(); the summaries must be written in it
NOTE = {"de": "KI-generierte Zusammenfassung. Maßgeblich ist der Originaltext.",
        "en": "AI-generated summary. The original text decides."}
BULLET_KINDS = ("WHO", "DUTY", "DEADLINE", "CONDITION", "OTHER")
DEPARTMENT_KINDS = ("WHY_MAX_BOEGL", "WHY_DEPARTMENT", "ATTENTION", "TO_CONFIRM")
MAX_BULLETS = 4
MAX_PARTS = 3                 # a long provision is summarised from the parts the screening kept
MAX_PART_REQUESTS = 8         # requests for the parts of one provision, including smaller pieces
MIN_LETTERS = 40              # a stored text with fewer letters is not sent to Qwen
NO_TEXT = {"de": "Der gespeicherte Text dieser Vorschrift enthält kaum Wörter. Bitte den Originaltext öffnen.",
           "en": "The stored text of this provision has almost no words. Please open the original text."}
MAX_ACT_HEADLINES = 12        # provisions shown to Qwen for one act summary
MAX_SENTENCES = 5
MAX_CARD_PROVISIONS = 15      # provisions shown to Qwen for one department summary
LIMITS = {"headline": 140, "overview": 700, "bullet": 380, "sentence": 340, "department_bullet": 450}


# ------------------------------------------------------------------ checks shared by all steps

def check_language(texts):
    if OUTPUT_LANGUAGE and wrong_language(texts, OUTPUT_LANGUAGE):
        raise Unclassified(f"Write everything in {LANGUAGES[OUTPUT_LANGUAGE]}: headline, overview and every bullet. "
                           "Translate the content of the text.")


def without_empty_statements(items, text_of):
    """Drop points whose message is only that the text does not say something (no deadline given ...)."""
    kept = [item for item in items if not states_nothing(text_of(item))]
    return kept or items[:1]


def check_numbers(texts, source):
    """Every number, date and number with unit in a summary must occur in the text it was written from."""
    problems = number_problems(texts, source)
    if problems:
        raise Unclassified(" ".join(problems[:3]))


def system_prompt(name, language):
    return PROMPTS[name].read_text(encoding="utf-8").replace("<LANGUAGE>", LANGUAGES[language]).strip() + "\n"


def ask(client, system, sent, task, schema, name, check, log, max_tokens):
    """One question to Qwen with at most one correction request. `check` validates the parsed reply."""
    evidence = json.dumps(sent, ensure_ascii=False)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": f"{evidence}\n\n{task}"}]
    for attempt in range(2):
        try:
            reply = client.chat(messages, thinking=False, stream=False, seed=42, sampling={"temperature": 0},
                                schema=schema, schema_name=name, max_tokens=max_tokens)
        except Exception as exc:  # noqa: BLE001 - the client could not send the request at all
            raise ConnectionLost(str(exc)) from exc
        usage = reply.usage or {}
        log.append({"seconds": round(getattr(reply, "seconds", 0.0) or 0.0, 3),
                    "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens")})
        if not reply.ok:
            if reply.error_kind == "context_limit":
                raise OverflowError
            raise ConnectionLost(client.clean(reply.error or f"Model request failed ({reply.error_kind})."))
        candidate, error = reply.parse_json()
        try:
            if error:
                raise Unclassified(error)
            if "final" in inspect.signature(check).parameters:
                return check(candidate, final=bool(attempt))
            return check(candidate)
        except Unclassified as exc:
            if attempt:
                raise Unclassified(f"Invalid answer after one correction request: {exc}") from exc
            messages[1]["content"] = (f"{evidence}\n\n{task}\n\nCORRECTION: your previous reply was rejected. "
                                      f"{exc} Return the complete JSON object again.")


# ------------------------------------------------------------------ step 1: provision summary

def provision_schema(passage_ids):
    return {
        "type": "object", "additionalProperties": False, "required": ["headline", "overview", "bullets"],
        "properties": {
            "headline": {"type": "string", "maxLength": LIMITS["headline"]},
            "overview": {"type": "string", "maxLength": LIMITS["overview"]},
            "bullets": {"type": "array", "minItems": 1, "maxItems": MAX_BULLETS, "items": {
                "type": "object", "additionalProperties": False, "required": ["kind", "text", "passages"],
                "properties": {
                    "kind": {"type": "string", "enum": list(BULLET_KINDS)},
                    "text": {"type": "string", "maxLength": LIMITS["bullet"]},
                    "passages": {"type": "array", "minItems": 1, "maxItems": 3,
                                 "items": {"type": "string", "enum": list(passage_ids)}},
                }}},
        },
    }


def validate_provision(reply, passages, source, locator="", final=False):
    """With final=True (the answer after the correction request) a bullet whose number, unit or date is still not
    in the text is left out instead of failing the whole provision; headline and overview must still be right."""
    if not isinstance(reply, dict) or set(reply) != {"headline", "overview", "bullets"}:
        raise Unclassified("The reply must hold exactly headline, overview and bullets.")
    headline, overview, bullets = reply["headline"], reply["overview"], reply["bullets"]
    if not isinstance(overview, str) or not squash(overview) or len(overview) > LIMITS["overview"]:
        raise Unclassified(f"overview is a text of 1 to {LIMITS['overview']} characters.")
    if not isinstance(headline, str) or not squash(headline) or len(headline) > LIMITS["headline"]:
        raise Unclassified(f"headline is a text of 1 to {LIMITS['headline']} characters.")
    if not isinstance(bullets, list) or not 1 <= len(bullets) <= MAX_BULLETS:
        raise Unclassified(f"bullets holds 1 to {MAX_BULLETS} entries.")
    result, seen = [], set()
    for bullet in bullets:
        if not isinstance(bullet, dict) or set(bullet) != {"kind", "text", "passages"}:
            raise Unclassified("Every bullet holds exactly kind, text and passages.")
        text = squash(bullet["text"]) if isinstance(bullet["text"], str) else ""
        if bullet["kind"] not in BULLET_KINDS or not text or len(text) > LIMITS["bullet"]:
            raise Unclassified(f"A bullet needs a kind from {', '.join(BULLET_KINDS)} and a text of at most "
                               f"{LIMITS['bullet']} characters.")
        cited = bullet["passages"]
        if not isinstance(cited, list) or not cited or any(mark not in passages for mark in cited):
            raise Unclassified("passages must name passage marks that occur in the target, for example P1.")
        if text.casefold() in seen:
            continue  # the same sentence twice is one bullet
        seen.add(text.casefold())
        result.append({"kind": bullet["kind"], "text": text, "passages": list(dict.fromkeys(cited))[:3]})
    result = without_empty_statements(result, lambda bullet: bullet["text"])
    check_language([squash(headline), squash(overview)])          # each group on its own: a German bullet list
    check_language([bullet["text"] for bullet in result])         # must not hide an English overview
    if final:
        known = Source(source)
        kept = [bullet for bullet in result if not number_problems([bullet["text"]], known)]
        if not kept:
            raise Unclassified("Every point has a number, unit or date that is not in the text.")
        dropped, result = len(result) - len(kept), kept
    else:
        dropped = 0
    check_numbers([squash(headline), squash(overview), *(bullet["text"] for bullet in result)], source)
    for bullet in result:
        cite_numbers(bullet, passages, locator)
    answer = {"headline": squash(headline), "overview": squash(overview), "bullets": result}
    if dropped:
        answer["points_left_out"] = dropped  # a point with a number, unit or date not in the text
    return answer


def cite_numbers(bullet, passages, locator):
    """Make the passages of a bullet hold its numbers.

    Every number is already known to be in the text that was sent. If it is not in the passages the bullet names,
    the passages that hold it are added (at most 3 in all). If it stands only outside the passages (title, opening
    words of a long annex), the bullet is marked so that the reader knows where the number comes from.
    """
    def missing(marks):
        return number_problems([bullet["text"]], " ".join([locator, *(passages[mark] for mark in marks)]))

    if not missing(bullet["passages"]):
        return
    for mark in passages:
        if len(bullet["passages"]) >= 3 or not missing(bullet["passages"]):
            break
        if mark not in bullet["passages"] and len(missing(bullet["passages"] + [mark])) < len(missing(bullet["passages"])):
            bullet["passages"].append(mark)
    if missing(bullet["passages"]):
        bullet["number_outside_passages"] = True


MERGE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["headline", "overview"],
    "properties": {"headline": {"type": "string", "maxLength": LIMITS["headline"]},
                   "overview": {"type": "string", "maxLength": LIMITS["overview"]}},
}


def validate_merge(reply, source):
    if not isinstance(reply, dict) or set(reply) != {"headline", "overview"}:
        raise Unclassified("The reply must hold exactly headline and overview.")
    headline, overview = (squash(reply[key]) if isinstance(reply[key], str) else "" for key in ("headline", "overview"))
    if not headline or not overview or len(headline) > LIMITS["headline"] or len(overview) > LIMITS["overview"]:
        raise Unclassified("headline and overview must be texts within their limits.")
    check_language([headline, overview])
    check_numbers([headline, overview], source)
    return {"headline": headline, "overview": overview}


def kept_parts(saved, count):
    """The numbers of the parts that the relevance screening did not remove."""
    parts = saved.get("model_attempt", {}).get("parts", [])
    kept = [part["part"] for part in parts if part.get("decision", {}).get("level") not in (None, "IGNORE")]
    numbered = [str(index) for index in range(1, count + 1)]
    return [part for part in numbered if part in kept] or numbered


def pick_bullets(bullets):
    """(shown, more): at most MAX_BULLETS shown, one kind after the other; the rest is kept, not dropped."""
    queues = {kind: [bullet for bullet in bullets if bullet["kind"] == kind] for kind in BULLET_KINDS}
    chosen = []
    while len(chosen) < MAX_BULLETS and any(queues.values()):
        for kind in BULLET_KINDS:
            if queues[kind] and len(chosen) < MAX_BULLETS:
                chosen.append(queues[kind].pop(0))
    shown = sorted(chosen, key=lambda bullet: BULLET_KINDS.index(bullet["kind"]))
    return shown, [bullet for bullet in bullets if bullet not in chosen]


def summarise_provision(client, system, saved, merge_system=None):
    """Summary of one provision from its text pack. Returns (summary, audit).

    A long provision is cut into parts. The parts that the relevance screening kept are read, at most MAX_PARTS.
    A part that is too long for the model is cut into smaller pieces and every piece is read. Whatever is not
    read is listed in summary["not_read"], so the reader sees it. With more than one part, a last request writes
    one headline and overview for all parts that were read.
    """
    pack = saved["evidence_pack"]
    provision = pack["provision"]
    full_text = squash(" ".join(target_text(provision)))
    if len(re.findall(r"[^\W\d_]", full_text)) < MIN_LETTERS:
        # Nothing to summarise (for example only "(7-146)"): no request, so Qwen cannot fill the gap from the title.
        return ({"headline": str(provision.get("locator", "")), "overview": NO_TEXT[OUTPUT_LANGUAGE or "en"],
                 "bullets": [], "text_missing": True}, {"parts_total": 0, "parts": [], "not_read": [], "requests": []})
    parts = split_target(provision, TARGET_CHARS)
    kept = kept_parts(saved, len(parts))
    opening = squash(" ".join(target_text(provision)))[:OPENING_CHARS]
    log, answers, not_read = [], [], []

    def run(part, part_id, budget):
        if len(log) >= MAX_PART_REQUESTS * 2:
            not_read.append({"part": part_id, "reason": "LIMIT"})
            return
        label = f"{part_id} of {len(parts)}" if len(parts) > 1 or "." in part_id else None
        packed, passages, _ = request_pack(pack, part, label, opening if label and part_id != "1" else None)
        # Only the act and the target: surrounding articles and references made Qwen summarise them as well.
        sent = {key: packed[key] for key in ("act", "source_notes", "target") if key in packed}
        opening_words = [item for item in packed.get("position", [])
                         if item.get("relation") == "opening words of the whole target"]
        if opening_words:
            sent["position"] = opening_words
        source, locator = json.dumps(sent, ensure_ascii=False), str(sent["target"].get("locator", ""))
        try:
            answer = ask(client, system, sent, TASKS["provision"], provision_schema(passages), "provision_summary",
                         lambda reply, final=False: validate_provision(reply, passages, source, locator, final),
                         log, 1100)
        except OverflowError:
            smaller = split_target(part, max(budget // 2, MIN_PART_CHARS))
            if len(smaller) < 2:
                not_read.append({"part": part_id, "reason": "TOO_LONG"})
                return
            for number, piece in enumerate(smaller, 1):  # every piece, not only the first
                run(piece, f"{part_id}.{number}", max(budget // 2, MIN_PART_CHARS))
            return
        for bullet in answer["bullets"]:
            bullet["part"] = part_id
            bullet["quotes"] = [passages[mark][:400] for mark in bullet["passages"]]
        answers.append({"part": part_id, **answer})

    for index, part in enumerate(parts, 1):
        part_id = str(index)
        if part_id not in kept:
            not_read.append({"part": part_id, "reason": "NOT_RELEVANT"})
        elif kept.index(part_id) >= MAX_PARTS:
            not_read.append({"part": part_id, "reason": "LIMIT"})
        else:
            run(part, part_id, TARGET_CHARS)
    if not answers:
        raise Unclassified("No part of the provision could be summarised.")
    shown, more = pick_bullets([bullet for answer in answers for bullet in answer["bullets"]])
    summary = {"headline": answers[0]["headline"], "overview": answers[0]["overview"], "bullets": shown}
    if more:
        summary["more_bullets"] = more
    if len(answers) > 1:
        merged = merge_parts(client, merge_system or system, pack, len(parts), answers, log)
        if merged:
            summary.update(merged)
        else:
            summary["overview_covers"] = answers[0]["part"]
    left_out = sum(answer.get("points_left_out", 0) for answer in answers)
    if left_out:
        summary["points_left_out"] = left_out
    if len(parts) > 1 or not_read:
        summary["parts_read"] = [answer["part"] for answer in answers]
        summary["parts_total"] = len(parts)
    if not_read:
        summary["not_read"] = not_read
    return summary, {"parts_total": len(parts), "parts": [a["part"] for a in answers], "not_read": not_read,
                     "requests": log}


def merge_parts(client, system, pack, total, answers, log):
    """One headline and overview from the summaries of all parts read. None when it fails."""
    sent = {"act": {"celex": pack["document"]["celex"], "title": pack["document"]["title"]},
            "provision": pack["provision"].get("locator", ""), "parts_total": total,
            "parts_read": [{"part": answer["part"], "headline": answer["headline"], "overview": answer["overview"],
                            "points": [bullet["text"] for bullet in answer["bullets"]]} for answer in answers]}
    source = json.dumps(sent, ensure_ascii=False)
    try:
        return ask(client, system, sent, TASKS["merge"], MERGE_SCHEMA, "provision_merge",
                   lambda reply: validate_merge(reply, source), log, 700)
    except (Unclassified, OverflowError):
        return None



def choose_folder(explicit):
    if explicit:
        return explicit.resolve()
    folders = sorted(p.parent for p in (ROOT / "runs").rglob("run_config.json")
                     if (p.parent / "provisions").is_dir() and (p.parent / "summary.json").is_file())
    if not folders:
        raise ValueError("No completed summary run in runs/. Supply --output-dir with its folder.")
    for i, folder in enumerate(folders, 1):
        report = read_json(folder / "summary.json")
        print(f"{i}. {folder} ({report.get('provisions_planned')} planned, "
              f"{report.get('provisions_failed')} originally failed)")
    if len(folders) == 1:
        return folders[0]
    choice = int(input("Number of the completed full run: "))
    if not 1 <= choice <= len(folders):
        raise ValueError("Folder number outside the list.")
    return folders[choice - 1]


def save_recovered(path, previous, record):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     suffix=".tmp", delete=False) as f:
        temp = Path(f.name)
    try:
        temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        if read_json(path) != previous:
            raise RuntimeError(f"Record changed during retry: {path}")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--runs", nargs="+", type=Path, default=DEFAULT_RUNS)
    parser.add_argument("--unsure-dir", type=Path, default=DEFAULT_UNSURE)
    parser.add_argument("--classification-dir", type=Path)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    out = choose_folder(args.output_dir)
    config = read_json(out / "run_config.json")
    global OUTPUT_LANGUAGE
    OUTPUT_LANGUAGE = config["language"]
    systems = {name: system_prompt(name, OUTPUT_LANGUAGE) for name in PROMPTS}
    fingerprint = sha([systems, TASKS, provision_schema(["P1"]), MERGE_SCHEMA, ACT_SCHEMA,
                       department_schema(["A"]), MAX_PARTS, MAX_PART_REQUESTS, MAX_CARD_PROVISIONS, OUTPUT_LANGUAGE])
    if fingerprint != config["fingerprint"]:
        raise ValueError("Prompt or schema differs from the original run.")
    failures = read_json(out / "summary.json")["failures"]
    keys = {(f["celex"], f["target"]) for f in failures}
    if not keys:
        print("No recorded failures.")
        return 0
    print(f"Locating source records for {len(keys)} reported failures...", flush=True)
    items, _ = collect([p.resolve() for p in args.runs], args.unsure_dir)
    by_key = {(item["celex"], item["target"]): item for item in items}
    errors = []
    todo = []
    for key in sorted(keys):
        item = by_key.get(key)
        path = record_path(out / "provisions", item) if item else None
        if not path or not path.is_file():
            errors.append(key)
            continue
        previous = read_json(path)
        if (previous.get("celex"), previous.get("target")) != key:
            errors.append(key)
        elif "summary" not in previous:
            if previous.get("failure"):
                todo.append((item, path))
            else:
                errors.append(key)
    if errors:
        raise ValueError(f"{len(errors)} records could not be reconciled. Check --runs and --unsure-dir. First: {errors[:3]}")
    print(f"{len(todo)} failures still pending in {out}", flush=True)
    todo = todo[:args.limit]
    if not todo:
        return 0
    classification = args.classification_dir or Path(config["classification"])
    classified = read_json(classification / "results.json")
    unsure_file = args.unsure_dir / "results.json"
    unsure = read_json(unsure_file) if unsure_file.is_file() else {}
    client = QwenClient.from_env(args.env_file, retries=1)
    ok, error = reachable(client)
    if not ok:
        raise ConnectionLost(error)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    log = out / f"provision_retry_{stamp}.jsonl"
    print(f"Retry report: {log}", flush=True)
    recovered = failed = 0
    start = time.monotonic()

    def progress(done, current):
        total = len(todo)
        n = 20 * done // total
        print(f"[{'#' * n}{'-' * (20 - n)}] {done}/{total} {100 * done / total:.1f}% | "
              f"recovered {recovered} | still failed {failed} | {time.monotonic()-start:.0f}s | {current}", flush=True)

    with log.open("x", encoding="utf-8") as report:
        for index, (item, path) in enumerate(todo):
            celex, target = item["celex"], item["target"]
            progress(index, f"{celex} {target}")
            old = read_json(path)
            if "summary" in old or not old.get("failure"):
                raise RuntimeError(f"Record changed since selection: {path}")
            event = {"celex": celex, "target": target, "original_error": old["failure"]}
            try:
                evidence = read_json(item["source"])
                result = classified[celex][target]
                pack = evidence["evidence_pack"]
                summary, audit = summarise_provision(client, systems["provision"], evidence, systems["merge"])
                record = dict(old, summary=summary, audit=audit, classification=result,
                              title=squash(pack["document"].get("title")),
                              text=squash(" ".join(target_text(pack["provision"])))[:600],
                              facts=provision_facts(item, evidence, result, unsure))
                del record["failure"]
            except ConnectionLost:
                print("Connection lost. Run the same command to resume.", flush=True)
                raise
            except Exception as exc:
                event.update(status="still_failed", error=f"{type(exc).__name__}: {exc}"[:500])
                failed += 1
            else:
                save_recovered(path, old, record)
                event.update(status="recovered", text_missing=summary.get("text_missing", False),
                             points_left_out=summary.get("points_left_out", 0))
                recovered += 1
            report.write(json.dumps(event, ensure_ascii=False) + "\n")
            report.flush()
    progress(len(todo), "finished")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
