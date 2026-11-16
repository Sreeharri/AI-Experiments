"""Decide the UNSURE provisions: each becomes INDIRECT or IGNORE.

The first screening left provisions UNSURE when the topic fitted but it could
not confirm that Max Bögl itself is addressed ("Does Max Bögl import steel
itself?"). Nobody can answer these questions now. This pass assumes "not
confirmed" and asks one thing: can the provision still reach Max Bögl through
its suppliers, purchased products, clients or markets?

    yes or maybe -> INDIRECT (the path is named)
    clearly no   -> IGNORE

The question of the first pass is kept with every provision ("To confirm: ...")
so that a department can later move it up to DIRECT.

Nothing in the screening run folders is changed. The results go to a new
folder. The command can be stopped with Ctrl+C and started again; finished
provisions are not asked again. When Qwen cannot be reached it waits 15, 30
and 45 minutes, then every 60 minutes, and continues by itself.

Test on 100 provisions first, then run all:

    python resolve_unsure_provisions.py --sample 100
    python resolve_unsure_provisions.py
"""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from eurlex_provision.profile import system_prompt  # noqa: E402
from eurlex_provision.simple import (MIN_PART_CHARS, OPENING_CHARS, TARGET_CHARS, request_pack,  # noqa: E402
                                     split_target, target_text)
from lr_ai.qwen_client import QwenClient  # noqa: E402
from screen_eurlex_provisions_simple import load_profile  # noqa: E402

PROMPT = ROOT / "prompts/eurlex_provision_unsure_resolve.md"
DEFAULT_RUNS = [ROOT / "runs/eurlex_provisions_baseline_v3_2", ROOT / "runs/eurlex_provisions_repair_v1"]
DEFAULT_OUTPUT = ROOT / "runs/eurlex_unsure_resolved_v1"
TASK = ("TASK: Decide whether this provision reaches Max Bögl's business through others. "
        "Return the JSON object described in the instructions.")
NONE = "NONE"
LIMITS = {"regulated_thing": 160, "reason": 500, "connection": 160}
WAIT_MINUTES = (15, 30, 45, 60)
QUICK_RETRIES = 3
MAX_WORKERS = 8


class ConnectionLost(Exception):
    """Qwen could not be reached. The run waits and continues."""


class Unresolved(Exception):
    """Qwen answered, but not with a usable decision for this provision."""


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


# ------------------------------------------------------------------ collecting

def batch_folders(run):
    return sorted(path for path in (run / "batches").glob("*") if (path / "audit" / "run_summary.json").is_file())


def collect(runs):
    """Find every UNSURE provision. An act screened in a later run counts only from that run."""
    owner = {}
    for position, run in enumerate(runs):
        folders = batch_folders(run)
        if not folders:
            raise ValueError(f"No screened batches found in {run}.")
        for folder in folders:
            manifest = folder / "manifest.json"
            for celex in (read_json(manifest) if manifest.is_file() else {}):
                owner[celex] = position
    found = []
    for position, run in enumerate(runs):
        for folder in batch_folders(run):
            for item in read_json(folder / "audit" / "run_summary.json").get("results", []):
                if item.get("level") == "UNSURE" and owner.get(item["celex"], position) == position:
                    found.append({"celex": item["celex"], "target": item["target"], "kind": item["kind"],
                                  "target_id": item["target_id"], "source": str(folder / item["file"]),
                                  "run": run.name})
    found.sort(key=lambda item: (item["celex"], item["target"], item["target_id"]))
    return found


def sample(items, count):
    """The same pseudo-random provisions every time, spread over the acts."""
    ordered = sorted(items, key=lambda item: sha([item["celex"], item["target"], item["target_id"]]))
    return sorted(ordered[:count], key=lambda item: (item["celex"], item["target"], item["target_id"]))


# ------------------------------------------------------------------ one request

def reply_schema(paths):
    return {
        "type": "object", "additionalProperties": False,
        "required": ["regulated_thing", "reason", "link", "connection"],
        "properties": {
            "regulated_thing": {"type": "string", "maxLength": LIMITS["regulated_thing"]},
            "reason": {"type": "string", "maxLength": LIMITS["reason"]},
            "link": {"type": "string", "enum": [*paths, NONE]},
            "connection": {"type": "string", "maxLength": LIMITS["connection"]},
        },
    }


def validate(reply, paths):
    if not isinstance(reply, dict) or set(reply) != {"regulated_thing", "reason", "link", "connection"}:
        raise Unresolved("The reply must hold exactly regulated_thing, reason, link and connection.")
    if reply["link"] not in (*paths, NONE):
        raise Unresolved(f"link must be one of {', '.join(paths)} or NONE.")
    for field, limit in LIMITS.items():
        if not isinstance(reply[field], str) or len(reply[field]) > limit:
            raise Unresolved(f"{field} is a text of at most {limit} characters.")
    if not reply["reason"].strip() or not reply["regulated_thing"].strip():
        raise Unresolved("regulated_thing and reason must not be empty.")
    if reply["link"] != NONE and not reply["connection"].strip():
        raise Unresolved("connection names the part of the business when link is a path.")
    return {"level": "IGNORE" if reply["link"] == NONE else "INDIRECT", "link": reply["link"],
            "regulated_thing": reply["regulated_thing"].strip(), "reason": reply["reason"].strip(),
            "connection": reply["connection"].strip() if reply["link"] != NONE else ""}


def ask(client, system, sent, paths, log):
    """One decision with at most one correction request."""
    evidence = json.dumps(sent, ensure_ascii=False)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": f"{evidence}\n\n{TASK}"}]
    for attempt in range(2):
        reply = client.chat(messages, thinking=False, stream=False, seed=42, sampling={"temperature": 0},
                            schema=reply_schema(paths), schema_name="unsure_resolution", max_tokens=600)
        log.append({"seconds": round(getattr(reply, "seconds", 0.0) or 0.0, 3),
                    "prompt_tokens": (reply.usage or {}).get("prompt_tokens"),
                    "completion_tokens": (reply.usage or {}).get("completion_tokens")})
        if not reply.ok:
            if reply.error_kind == "context_limit":
                raise OverflowError
            raise ConnectionLost(client.clean(reply.error or f"Model request failed ({reply.error_kind})."))
        candidate, error = reply.parse_json()
        try:
            if error:
                raise Unresolved(error)
            return validate(candidate, paths), reply.content
        except Unresolved as exc:
            if attempt:
                raise Unresolved(f"Invalid answer after one correction request: {exc}") from exc
            messages[1]["content"] = (f"{evidence}\n\n{TASK}\n\nCORRECTION: your previous reply was rejected. "
                                      f"{exc} Return the complete JSON object again.")


def first_findings(saved, part_id=None):
    """What the first pass wrote about the provision, or about one of its parts."""
    details = saved.get("details", {})
    for part in saved.get("model_attempt", {}).get("parts", []):
        if part.get("part") == part_id:
            details = part.get("evidence", details)
    return {field: details[field] for field in ("topic", "subject", "actor", "open_question") if details.get(field)}


def resolve(client, system, paths, saved):
    """Decide one provision, part by part when it is long. Returns (decision, audit)."""
    pack = saved["evidence_pack"]
    provision = pack["provision"]
    parts = split_target(provision, TARGET_CHARS)
    opening = " ".join(" ".join(target_text(provision)).split())[:OPENING_CHARS]
    first_parts = saved.get("model_attempt", {}).get("parts", [])
    unsure = {part["part"] for part in first_parts if part.get("decision", {}).get("level") == "UNSURE"}
    numbered = {str(index) for index in range(1, len(parts) + 1)}
    # Parts that the first pass already removed stay removed. Ask again only where it was unsure.
    wanted = unsure if unsure and unsure <= numbered else numbered
    log, answers = [], []

    def run(part, part_id, budget):
        label = f"{part_id} of {len(parts)}" if len(parts) > 1 or "." in part_id else None
        sent, _, _ = request_pack(pack, part, label, opening if label and part_id != "1" else None)
        if findings := first_findings(saved, part_id):
            sent["first_pass"] = findings
        try:
            decision, raw = ask(client, system, sent, paths, log)
        except OverflowError:
            smaller = split_target(part, max(budget // 2, MIN_PART_CHARS))
            if len(smaller) < 2:
                raise Unresolved(f"Part {part_id} cannot be made small enough for the model.") from None
            return any(run(piece, f"{part_id}.{index}", max(budget // 2, MIN_PART_CHARS))
                       for index, piece in enumerate(smaller, 1))
        answers.append({"part": part_id, **decision, "raw_reply": raw})
        return decision["level"] == "INDIRECT"

    for index, part in enumerate(parts, 1):
        if str(index) in wanted and run(part, str(index), TARGET_CHARS):
            break  # One part that reaches the business is enough to keep the provision.
    kept = [answer for answer in answers if answer["level"] == "INDIRECT"]
    winner = (kept or answers)[0]
    decision = {field: winner[field] for field in ("level", "link", "regulated_thing", "reason", "connection")}
    if len(parts) > 1:
        decision["reason"] = (f"Decided in parts; part {winner['part']} of {len(parts)}. " + decision["reason"])[:600]
    return decision, {"parts": answers, "requests": log}


# ------------------------------------------------------------------ the run

class Progress:
    def __init__(self, total, done, counts):
        self.total, self.done, self.counts = total, done, counts
        self.start, self.start_done, self.lock = time.monotonic(), done, threading.Lock()

    def step(self, level):
        with self.lock:
            self.done += 1
            self.counts[level] += 1
            if self.done % 20 == 0 or self.done == self.total:
                self.show()

    def show(self):
        elapsed = time.monotonic() - self.start
        fresh = self.done - self.start_done
        rate = fresh / elapsed * 3600 if elapsed > 30 and fresh else 0
        left = self.total - self.done
        end = (datetime.now() + timedelta(hours=left / rate)).strftime("%a %H:%M") if rate and left else "-"
        filled = round(24 * self.done / self.total) if self.total else 24
        print(f"[{'#' * filled}{'.' * (24 - filled)}] {self.done / max(self.total, 1):6.1%}  "
              f"{self.done:,}/{self.total:,}  INDIRECT {self.counts['INDIRECT']:,}  "
              f"IGNORE {self.counts['IGNORE']:,}  failed {self.counts['FAILED']:,}  "
              f"{rate:,.0f}/h  ends about {end}", flush=True)


def record_path(out, item):
    name = f"{item['kind'].lower()}_{sha([item['target_id'], item['target']])[:18]}.json"
    return out / "targets" / item["celex"] / name


def one_pass(client, system, paths, out, todo, progress, workers):
    """Ask Qwen for every open provision. Returns the connection error that stopped the pass, if any."""
    stop = threading.Event()

    def work(item):
        if stop.is_set():
            return
        saved = read_json(item["source"])
        first = {"level": saved["level"], "reason": saved["reason"], "area": saved.get("area", []),
                 "open_question": saved.get("details", {}).get("open_question", "")}
        record = {**{key: item[key] for key in ("celex", "target", "kind", "target_id", "run")}, "first": first,
                  "text": " ".join(" ".join(target_text(saved["evidence_pack"]["provision"])).split())[:600]}
        try:
            decision, audit = resolve(client, system, paths, saved)
        except Unresolved as exc:
            record["failure"] = str(exc)
            print(f"Could not decide {item['celex']} {item['target']}: {exc}", flush=True)
            level = "FAILED"
        except ConnectionLost:
            stop.set()
            raise
        else:
            record.update(resolved=decision, audit=audit)
            level = decision["level"]
        write_json(record_path(out, item), record)
        progress.step(level)

    lost = None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, item) for item in todo]
        try:
            for future in as_completed(futures):
                try:
                    future.result()
                except ConnectionLost as exc:
                    lost = lost or exc
        except BaseException:
            stop.set()  # Ctrl+C: running requests finish and are saved, the rest is skipped.
            raise
    return lost


def export(out, items):
    """Write results.json, summary.json and review.csv from the saved records."""
    results, rows, failures = {}, [], []
    levels, links, areas = Counter(), Counter(), Counter()
    for item in items:
        path = record_path(out, item)
        if not path.is_file():
            continue
        record = read_json(path)
        if "resolved" not in record:
            failures.append({"celex": record["celex"], "target": record["target"], "error": record.get("failure")})
            continue
        decision, first = record["resolved"], record["first"]
        question = first.get("open_question", "")
        area = first["area"] if decision["level"] == "INDIRECT" else []
        results.setdefault(record["celex"], {})[record["target"]] = {
            "level": decision["level"], "reason": decision["reason"], "area": area,
            "indirect_path": decision["link"] if decision["level"] == "INDIRECT" else None,
            "connection": decision["connection"], "previous_level": "UNSURE",
            **({"to_confirm": question} if question and decision["level"] == "INDIRECT" else {}),
        }
        levels[decision["level"]] += 1
        links[decision["link"]] += 1
        areas[((first["area"] or ["-"])[0], decision["level"])] += 1
        rows.append([record["celex"], record["target"], (first["area"] or [""])[0], question, decision["level"],
                     decision["link"], decision["regulated_thing"], decision["connection"], decision["reason"],
                     record.get("text", "")[:400]])
    write_json(out / "results.json", results)
    summary = {
        "provisions_planned": len(items), "provisions_decided": sum(levels.values()),
        "provisions_failed": len(failures), "provisions_open": len(items) - sum(levels.values()) - len(failures),
        "counts": dict(levels), "paths": dict(links),
        "by_business_area": {area: {level: count for (name, level), count in sorted(areas.items()) if name == area}
                             for area in sorted({name for name, _ in areas})},
        "failures": failures,
    }
    write_json(out / "summary.json", summary)
    with (out / "review.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["celex", "provision", "business_area", "question_of_first_pass", "new_level", "path",
                         "regulated_thing", "connection", "reason", "text_start", "correct (yes/no)", "comment"])
        writer.writerows(rows)
    return summary


def reachable(client):
    """Ask the endpoint for its model list. This also sets the address the client sends requests to."""
    try:
        found = client.discover()
    except Exception as exc:  # noqa: BLE001 - any failure of the check means "not reachable yet"
        return False, str(exc)
    return bool(found.get("ok")), str(found.get("error") or "")


def wait_until_reachable(client, why, step=0):
    """Wait 15, 30, 45 minutes, then every 60 minutes, until Qwen answers. Returns nothing."""
    while True:
        minutes = WAIT_MINUTES[min(step, len(WAIT_MINUTES) - 1)]
        step += 1
        print(f"{datetime.now():%Y-%m-%d %H:%M}  Qwen cannot be reached ({str(why)[:160]}). "
              f"Waiting {minutes:g} min, then checking again.", flush=True)
        time.sleep(minutes * 60)
        ok, error = reachable(client)
        if ok:
            print(f"{datetime.now():%Y-%m-%d %H:%M}  Qwen answers again. Continuing.", flush=True)
            return
        why = error or why


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=Path, nargs="+", default=DEFAULT_RUNS,
                        help="Screening run folders, oldest first. An act screened in a later run counts "
                             "only from that run.")
    parser.add_argument("--anchors", type=Path, default=ROOT / "config/business_anchors_en.yaml")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--sample", type=int, help="Decide only this many provisions (a fixed random choice).")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--list-only", action="store_true", help="Count the UNSURE provisions and stop.")
    args = parser.parse_args()
    out = args.output_dir.resolve()

    try:
        items = collect([run.resolve() for run in args.runs])
        profile = load_profile(args.anchors)
        system = system_prompt(PROMPT.read_text(encoding="utf-8"), "", profile)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 1
    paths = list(profile["indirect_paths"])
    print(f"{len(items):,} UNSURE provisions found in {len(args.runs)} run folders.", flush=True)
    if args.list_only:
        return 0

    fingerprint = sha([system, TASK, reply_schema(paths)])
    config = out / "run_config.json"
    if config.is_file() and read_json(config).get("fingerprint") != fingerprint:
        print("The prompt, anchors or settings changed since this folder was started. "
              "Use a new --output-dir.", file=sys.stderr)
        return 1
    write_json(config, {"fingerprint": fingerprint, "runs": [str(run) for run in args.runs],
                        "system_prompt": system})
    planned = sample(items, args.sample) if args.sample else items
    if args.sample:
        print(f"Sample: {len(planned):,} provisions.", flush=True)

    client = QwenClient.from_env(args.env_file, retries=1)
    workers = max(1, min(args.workers, MAX_WORKERS))
    quick = 0
    try:
        # The client learns the address and model name from the endpoint before the first request.
        ok, error = reachable(client)
        if not ok:
            wait_until_reachable(client, error)
        while True:
            counts, todo = Counter(), []
            for item in planned:
                path = record_path(out, item)
                record = read_json(path) if path.is_file() else {}
                if "resolved" in record:
                    counts[record["resolved"]["level"]] += 1
                else:
                    todo.append(item)  # New, or failed before: failed ones get one more try per start.
            if not todo:
                break
            done_before = len(planned) - len(todo)
            progress = Progress(len(planned), done_before, counts)
            progress.show()
            lost = one_pass(client, system, paths, out, todo, progress, workers)
            export(out, planned)
            if lost is None:
                break
            if progress.done > done_before:
                quick = 0
            ok, error = reachable(client)
            if ok and quick < QUICK_RETRIES:
                quick += 1
                print(f"{datetime.now():%Y-%m-%d %H:%M}  A request failed ({str(lost)[:160]}). "
                      "Qwen answers, continuing in 15 s.", flush=True)
                time.sleep(15)
            else:
                wait_until_reachable(client, error or lost)
                quick = 0
    except KeyboardInterrupt:
        export(out, planned)
        print("\nStopped by Ctrl+C. Run the same command to continue.", flush=True)
        return 130

    summary = export(out, planned)
    counts = summary["counts"]
    print(f"\nFinished: {summary['provisions_decided']:,} of {len(planned):,} decided. "
          f"INDIRECT {counts.get('INDIRECT', 0):,}, IGNORE {counts.get('IGNORE', 0):,}, "
          f"failed {summary['provisions_failed']:,}.")
    print(f"Results: {out / 'results.json'}\nTo check by hand: {out / 'review.csv'}")
    if summary["provisions_failed"]:
        print("Run the same command again to retry the failed ones.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
