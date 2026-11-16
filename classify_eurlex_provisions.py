"""Classify relevant provisions into legal domains.

Input: every provision that the relevance screening kept as DIRECT or INDIRECT
(including the UNSURE provisions that the UNSURE pass turned into INDIRECT).

Two steps, both with Qwen:

1. Each legal act gets one domain from its title and the first words of its
   provisions. Most provisions belong to the domain of their act.
2. Each provision is classified with the act's domain as the starting point:

       primary domain   + reason
       secondary domain + reason   (only for a second duty of its own)

Qwen sees the provision text and the domain list. It does not see the relevance
reason or the business area; the code uses the business area afterwards as an
independent check. The code also adds the departments of each domain.

Nothing in the screening run folders is changed. The results go to a new
folder. The command can be stopped with Ctrl+C and started again; finished
acts and provisions are not asked again. When Qwen cannot be reached it waits
15, 30 and 45 minutes, then every 60 minutes, and continues by itself.

    python classify_eurlex_provisions.py --count-only        # how many provisions, no Qwen
    python classify_eurlex_provisions.py --sample 200        # pilot
    python classify_eurlex_provisions.py --levels DIRECT     # all DIRECT provisions
    python classify_eurlex_provisions.py                     # everything (continues)
"""

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import threading
import time

import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from eurlex_provision.simple import (MIN_PART_CHARS, OPENING_CHARS, TARGET_CHARS, request_pack,  # noqa: E402
                                     split_target, target_text)
from lr_ai.qwen_client import QwenClient  # noqa: E402

PROMPT = ROOT / "prompts/eurlex_provision_classification.md"
ACT_PROMPT = ROOT / "prompts/eurlex_act_domain.md"
DEFAULT_RUNS = [ROOT / "runs/eurlex_provisions_baseline_v3_2", ROOT / "runs/eurlex_provisions_repair_v1"]
DEFAULT_UNSURE = ROOT / "runs/eurlex_unsure_resolved_v1"
DEFAULT_OUTPUT = ROOT / "runs/eurlex_classification_v5"
TASK = "TASK: Assign this provision to a legal domain. Return the JSON object described in the instructions."
ACT_TASK = "TASK: Assign this legal act to a legal domain. Return the JSON object described in the instructions."
NO_FIT = "no_fitting_domain"
MIXED = "mixed_act"
ACT_PROVISIONS = 12      # openings of this many provisions describe the act
ACT_OPENING_CHARS = 260
NO_SECOND = "none"
NO_DEPARTMENT = "(no department yet)"
LIMITS = {"subject": 200, "primary_reason": 450, "secondary_reason": 450}
ACT_LIMITS = {"act_subject": 260, "reason": 450}
MAX_PARTS = 5            # a long annex is classified from its first parts
MAX_REQUESTS = 12        # per provision, including parts that had to be made smaller
WAIT_MINUTES = (15, 30, 45, 60)
QUICK_RETRIES = 3
MAX_WORKERS = 8
KEPT = ("DIRECT", "INDIRECT")


class ConnectionLost(Exception):
    """Qwen could not be reached. The run waits and continues."""


class Unclassified(Exception):
    """Qwen answered, but not with a usable classification for this provision."""


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def squash(text):
    return " ".join(str(text or "").split())


# ------------------------------------------------------------------ domain catalogue

def load_domains(english, german, anchors):
    """Read both domain files and the anchor file. Returns everything the run needs about domains."""
    en = yaml.safe_load(Path(english).read_text(encoding="utf-8-sig"))
    de = yaml.safe_load(Path(german).read_text(encoding="utf-8-sig"))
    domains = en.get("domains") or {}
    if not domains or list(domains) != list(de.get("domains") or {}):
        raise ValueError("domains_en.yaml and domains_de.yaml must list the same domains in the same order. "
                         "Check them with tools/check_domains.py.")
    for key, domain in domains.items():
        if not squash(domain.get("scope")) or not squash(domain.get("title")):
            raise ValueError(f"Domain {key} needs a title and a scope.")
    if {NO_FIT, NO_SECOND, MIXED} & set(domains):
        raise ValueError(f"{NO_FIT}, {NO_SECOND} and {MIXED} are reserved words and cannot be domain IDs.")
    catalog = yaml.safe_load(Path(anchors).read_text(encoding="utf-8-sig")).get("anchors") or {}
    overrides = en.get("anchor_domain_overrides") or {}
    anchor_domain = {}
    for anchor, definition in catalog.items():
        domain = overrides[anchor] if anchor in overrides else definition.get("domain")
        if domain in domains:
            anchor_domain[anchor] = domain
    return {
        "version": en.get("version"), "ids": list(domains), "rules": [squash(rule) for rule in
                                                                      en.get("classification_rules") or []],
        "hints": [squash(hint) for hint in en.get("routing_hints") or []],
        "english": {key: {"title": squash(d.get("label") or d["title"]), "scope": squash(d["scope"]),
                          "distinguish": squash(d.get("distinguish"))} for key, d in domains.items()},
        "fallback": squash(((en.get("fallback") or {}).get(NO_FIT) or {}).get("scope"))
        or "The subject of the provision belongs to none of the legal domains.",
        "titles": {key: squash(d.get("title")) for key, d in de["domains"].items()},
        "departments": {key: list(d.get("departments") or []) for key, d in de["domains"].items()},
        "anchor_domain": anchor_domain,
    }


def system_prompt(instructions, domains):
    """Instructions, rules, then the domain list. The same text for every request, so it can be cached."""
    rules = "\n".join(f"- {rule}" for rule in domains["rules"])
    if domains["hints"]:
        rules += "\n\nWHERE FREQUENT SUBJECTS BELONG\n" + "\n".join(f"- {hint}" for hint in domains["hints"])
    entries = []
    for key, domain in domains["english"].items():
        entry = f"- {key} | {domain['title']}\n  Covers: {domain['scope']}"
        if domain["distinguish"]:
            entry += f"\n  Boundary: {domain['distinguish']}"
        entries.append(entry)
    entries.append(f"- {NO_FIT}\n  Use when: {domains['fallback']}")
    return f"{instructions.strip()}\n\nRULES\n{rules}\n\nLEGAL DOMAINS\n" + "\n".join(entries) + "\n"


# ------------------------------------------------------------------ collecting

def batch_folders(run):
    return sorted(path for path in (run / "batches").glob("*") if (path / "audit" / "run_summary.json").is_file())


def collect(runs, unsure_dir):
    """Find every kept provision. Returns (items, facts about what was found)."""
    owner = {}
    for position, run in enumerate(runs):
        folders = batch_folders(run)
        if not folders:
            raise ValueError(f"No screened batches found in {run}.")
        for folder in folders:
            manifest = folder / "manifest.json"
            for celex in (read_json(manifest) if manifest.is_file() else {}):
                owner[celex] = position   # An act screened in a later run counts only from that run.
    resolved = {}
    unsure_file = Path(unsure_dir) / "results.json" if unsure_dir else None
    if unsure_file and unsure_file.is_file():
        resolved = read_json(unsure_file)
    items, facts = [], Counter()
    for position, run in enumerate(runs):
        for folder in batch_folders(run):
            for item in read_json(folder / "audit" / "run_summary.json").get("results", []):
                if owner.get(item["celex"], position) != position:
                    continue
                level, extra = item.get("level"), {}
                if level == "UNSURE":
                    decision = resolved.get(item["celex"], {}).get(item["target"])
                    if decision is None:
                        facts["unsure_without_decision"] += 1
                        continue
                    level = decision["level"]
                    if level in KEPT:
                        facts["from_unsure_pass"] += 1
                        extra = {"area": decision.get("area") or item.get("area") or [],
                                 "to_confirm": decision.get("to_confirm", "")}
                if level not in KEPT:
                    continue
                items.append({"celex": item["celex"], "target": item["target"], "kind": item["kind"],
                              "target_id": item["target_id"], "source": str(folder / item["file"]),
                              "run": run.name, "level": level, "area": list(item.get("area") or []), **extra})
                facts[level] += 1
    items.sort(key=lambda item: (item["celex"], item["target"], item["target_id"]))
    return items, facts


def sample(items, count):
    """A fixed choice: about 70% DIRECT and 30% INDIRECT, spread over the business areas."""
    def spread(group, wanted):
        by_area = defaultdict(list)
        for item in sorted(group, key=lambda item: sha([item["celex"], item["target"], item["target_id"]])):
            by_area[(item["area"] or ["-"])[0]].append(item)
        chosen, queues = [], [by_area[area] for area in sorted(by_area)]
        while len(chosen) < wanted and any(queues):
            for queue in queues:
                if queue and len(chosen) < wanted:
                    chosen.append(queue.pop(0))
        return chosen

    direct = [item for item in items if item["level"] == "DIRECT"]
    indirect = [item for item in items if item["level"] == "INDIRECT"]
    first = spread(direct, min(len(direct), round(count * 0.7) if indirect else count))
    chosen = first + spread(indirect, count - len(first))
    if len(chosen) < count:
        rest = [item for item in direct if item not in chosen]
        chosen += spread(rest, count - len(chosen))
    return sorted(chosen, key=lambda item: (item["celex"], item["target"], item["target_id"]))


# ------------------------------------------------------------------ one request

def reply_schema(ids):
    return {
        "type": "object", "additionalProperties": False,
        "required": ["subject", "primary_domain", "primary_reason", "secondary_domain", "secondary_reason"],
        "properties": {
            "subject": {"type": "string", "maxLength": LIMITS["subject"]},
            "primary_domain": {"type": "string", "enum": [*ids, NO_FIT]},
            "primary_reason": {"type": "string", "maxLength": LIMITS["primary_reason"]},
            "secondary_domain": {"type": "string", "enum": [*ids, NO_SECOND]},
            "secondary_reason": {"type": "string", "maxLength": LIMITS["secondary_reason"]},
        },
    }


def validate(reply, ids):
    """Check one answer and bring it into its simplest correct form."""
    fields = ("subject", "primary_domain", "primary_reason", "secondary_domain", "secondary_reason")
    if not isinstance(reply, dict) or set(reply) != set(fields):
        raise Unclassified("The reply must hold exactly " + ", ".join(fields) + ".")
    if any(not isinstance(reply[field], str) for field in fields):
        raise Unclassified("Every field is a text.")
    if reply["primary_domain"] not in (*ids, NO_FIT):
        raise Unclassified(f"primary_domain must be a domain id or {NO_FIT}.")
    if reply["secondary_domain"] not in (*ids, NO_SECOND):
        raise Unclassified(f"secondary_domain must be a domain id or {NO_SECOND}.")
    for field, limit in LIMITS.items():
        if len(reply[field]) > limit:
            raise Unclassified(f"{field} has at most {limit} characters.")
    answer = {field: squash(reply[field]) for field in fields}
    if not answer["subject"] or not answer["primary_reason"]:
        raise Unclassified("subject and primary_reason must not be empty.")
    if answer["secondary_domain"] == answer["primary_domain"]:
        answer["secondary_domain"] = NO_SECOND       # The same domain twice is one domain.
    if answer["primary_domain"] == NO_FIT and answer["secondary_domain"] != NO_SECOND:
        if not answer["secondary_reason"]:
            raise Unclassified("A secondary domain needs its own reason.")
        # "No domain fits, but this one does" means that one is the domain.
        answer.update(primary_domain=answer["secondary_domain"], primary_reason=answer["secondary_reason"],
                      secondary_domain=NO_SECOND)
    if answer["secondary_domain"] == NO_SECOND:
        answer["secondary_reason"] = ""
    elif not answer["secondary_reason"]:
        raise Unclassified("A secondary domain needs its own reason in secondary_reason.")
    return answer


def act_schema(ids):
    return {
        "type": "object", "additionalProperties": False, "required": ["act_subject", "reason", "act_domain"],
        "properties": {
            "act_subject": {"type": "string", "maxLength": ACT_LIMITS["act_subject"]},
            "reason": {"type": "string", "maxLength": ACT_LIMITS["reason"]},
            "act_domain": {"type": "string", "enum": [*ids, NO_FIT, MIXED]},
        },
    }


def validate_act(reply, ids):
    if not isinstance(reply, dict) or set(reply) != {"act_subject", "reason", "act_domain"}:
        raise Unclassified("The reply must hold exactly act_subject, reason and act_domain.")
    if any(not isinstance(value, str) for value in reply.values()):
        raise Unclassified("Every field is a text.")
    if reply["act_domain"] not in (*ids, NO_FIT, MIXED):
        raise Unclassified(f"act_domain must be a domain id, {NO_FIT} or {MIXED}.")
    for field, limit in ACT_LIMITS.items():
        if len(reply[field]) > limit:
            raise Unclassified(f"{field} has at most {limit} characters.")
    answer = {field: squash(reply[field]) for field in ("act_subject", "reason", "act_domain")}
    if not answer["act_subject"] or not answer["reason"]:
        raise Unclassified("act_subject and reason must not be empty.")
    return answer


def ask(client, system, sent, task, schema, name, check, log):
    """One question to Qwen with at most one correction request. `check` validates the parsed reply."""
    evidence = json.dumps(sent, ensure_ascii=False)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": f"{evidence}\n\n{task}"}]
    for attempt in range(2):
        try:
            reply = client.chat(messages, thinking=False, stream=False, seed=42, sampling={"temperature": 0},
                                schema=schema, schema_name=name, max_tokens=500)
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
            return check(candidate)
        except Unclassified as exc:
            if attempt:
                raise Unclassified(f"Invalid answer after one correction request: {exc}") from exc
            messages[1]["content"] = (f"{evidence}\n\n{task}\n\nCORRECTION: your previous reply was rejected. "
                                      f"{exc} Return the complete JSON object again.")


def act_evidence(celex, sources):
    """What describes an act: its title and the first words of some of its provisions."""
    step = max(1, math.ceil(len(sources) / ACT_PROVISIONS))
    title, provisions = "", []
    for source in sources[::step][:ACT_PROVISIONS]:
        try:
            pack = read_json(source)["evidence_pack"]
        except (OSError, ValueError, KeyError):
            continue
        title = title or squash(pack["document"].get("title"))
        provisions.append({"locator": pack["provision"].get("locator", ""),
                           "opening": squash(" ".join(target_text(pack["provision"])))[:ACT_OPENING_CHARS]})
    if not title and not provisions:
        raise Unclassified("No saved provision of this act could be read.")
    return {"act": {"celex": celex, "title": title}, "provisions": provisions}


def classify_act(client, system, ids, celex, sources):
    """The domain of one act as a whole. Returns (answer, evidence title, request log)."""
    sent, log = act_evidence(celex, sources), []
    try:
        answer = ask(client, system, sent, ACT_TASK, act_schema(ids), "act_domain",
                     lambda reply: validate_act(reply, ids), log)
    except OverflowError:
        sent["provisions"] = sent["provisions"][:3]
        try:
            answer = ask(client, system, sent, ACT_TASK, act_schema(ids), "act_domain",
                         lambda reply: validate_act(reply, ids), log)
        except OverflowError:
            raise Unclassified("The act description is too long for the model.") from None
    return answer, sent["act"]["title"], log


def combine(answers):
    """One classification from the answers for the parts of a provision."""
    if len(answers) == 1:
        return {**answers[0], "parts_disagree": False}
    votes = Counter(a["primary_domain"] for a in answers if a["primary_domain"] != NO_FIT)
    if not votes:
        return {**answers[0], "parts_disagree": False}
    top = max(votes.values())
    primary = next(a["primary_domain"] for a in answers if votes.get(a["primary_domain"]) == top)
    lead = next(a for a in answers if a["primary_domain"] == primary)
    # A second domain needs at least a quarter of the parts behind it.
    mentions = Counter()
    for answer in answers:
        mentions.update({answer["primary_domain"], answer["secondary_domain"]} - {primary, NO_FIT, NO_SECOND})
    needed = math.ceil(len(answers) / 4)
    secondary, secondary_reason = NO_SECOND, ""
    if mentions and max(mentions.values()) >= needed:
        best = max(mentions.values())
        for answer in answers:
            if mentions.get(answer["primary_domain"]) == best:
                secondary, secondary_reason = answer["primary_domain"], answer["primary_reason"]
                break
            if mentions.get(answer["secondary_domain"]) == best:
                secondary, secondary_reason = answer["secondary_domain"], answer["secondary_reason"]
                break
    return {"subject": lead["subject"], "primary_domain": primary, "primary_reason": lead["primary_reason"],
            "secondary_domain": secondary, "secondary_reason": secondary_reason,
            "parts_disagree": len(votes) > 1}


def classify(client, system, ids, pack, act=None):
    """Classify one provision, from its first parts when it is long. Returns (classification, audit).

    `act` is the saved act-level answer; its domain is the starting point for the provision.
    """
    provision = pack["provision"]
    parts = split_target(provision, TARGET_CHARS)
    opening = squash(" ".join(target_text(provision)))[:OPENING_CHARS]
    log, answers = [], []

    def run(part, part_id, budget):
        if len(log) >= MAX_REQUESTS:
            return
        label = f"{part_id} of {len(parts)}" if len(parts) > 1 or "." in part_id else None
        packed, _, _ = request_pack(pack, part, label, opening if label and part_id != "1" else None)
        sent = {"act": packed.pop("act")}
        if act and act.get("act_domain"):
            sent["act_domain"] = {"domain": act["act_domain"], "act_subject": act.get("act_subject", "")}
        sent.update(packed)
        try:
            answer = ask(client, system, sent, TASK, reply_schema(ids), "provision_classification",
                         lambda reply: validate(reply, ids), log)
        except OverflowError:
            smaller = split_target(part, max(budget // 2, MIN_PART_CHARS))
            if len(smaller) < 2:
                raise Unclassified(f"Part {part_id} cannot be made small enough for the model.") from None
            for index, piece in enumerate(smaller[:2], 1):
                run(piece, f"{part_id}.{index}", max(budget // 2, MIN_PART_CHARS))
            return
        answers.append({"part": part_id, **answer})

    for index, part in enumerate(parts[:MAX_PARTS], 1):
        run(part, str(index), TARGET_CHARS)
    if not answers:
        raise Unclassified("No part of the provision could be classified.")
    result = combine([{k: v for k, v in answer.items() if k != "part"} for answer in answers])
    audit = {"parts_total": len(parts), "parts_asked": len(answers), "parts": answers, "requests": log}
    return result, audit


def finish(classification, item, domains, act=None):
    """Add what the code knows: departments, German titles and the check flags."""
    primary, secondary = classification["primary_domain"], classification["secondary_domain"]
    expected = sorted({domains["anchor_domain"][a] for a in item["area"] if a in domains["anchor_domain"]})
    check = []
    if primary == NO_FIT:
        check.append("no_fitting_domain")
    elif expected and primary not in expected and secondary not in expected:
        check.append("differs_from_business_area")
    if classification.get("parts_disagree"):
        check.append("parts_disagree")
    return {
        "level": item["level"], "subject": classification["subject"],
        "primary_domain": primary, "primary_title": domains["titles"].get(primary, ""),
        "primary_reason": classification["primary_reason"],
        "secondary_domain": None if secondary == NO_SECOND else secondary,
        "secondary_title": domains["titles"].get(secondary, ""),
        "secondary_reason": classification["secondary_reason"],
        "departments": domains["departments"].get(primary, []),
        "secondary_departments": domains["departments"].get(secondary, []),
        "check": check, "act_domain": (act or {}).get("act_domain"),
        "business_area": item["area"], "business_area_domains": expected,
        **({"to_confirm": item["to_confirm"]} if item.get("to_confirm") else {}),
    }


# ------------------------------------------------------------------ the run

class Progress:
    def __init__(self, total, done, name="Provisions"):
        self.total, self.done, self.failed, self.flagged, self.name = total, done, 0, 0, name
        self.start, self.start_done, self.lock = time.monotonic(), done, threading.Lock()

    def step(self, failed=False, flagged=False):
        with self.lock:
            self.done += 1
            self.failed += failed
            self.flagged += flagged
            if self.done % 20 == 0 or self.done == self.total:
                self.show()

    def show(self):
        elapsed = time.monotonic() - self.start
        fresh = self.done - self.start_done
        rate = fresh / elapsed * 3600 if elapsed > 30 and fresh else 0
        left = self.total - self.done
        end = (datetime.now() + timedelta(hours=left / rate)).strftime("%a %H:%M") if rate and left else "-"
        filled = round(24 * self.done / self.total) if self.total else 24
        print(f"{self.name:<10} [{'#' * filled}{'.' * (24 - filled)}] {self.done / max(self.total, 1):6.1%}  "
              f"{self.done:,}/{self.total:,}  to check {self.flagged:,}  failed {self.failed:,}  "
              f"{rate:,.0f}/h  ends about {end}", flush=True)


def record_path(out, item):
    name = f"{item['kind'].lower()}_{sha([item['target_id'], item['target']])[:18]}.json"
    return out / "targets" / item["celex"] / name


def act_path(out, celex):
    return out / "acts" / f"{safe_name(celex)}.json"


def read_act(out, celex):
    path = act_path(out, celex)
    return read_json(path) if path.is_file() else None


def run_pool(todo, work, workers):
    """Run `work` for every item. Returns the connection error that stopped the pass, if any."""
    stop = threading.Event()

    def guarded(item):
        if stop.is_set():
            return
        try:
            work(item)
        except ConnectionLost:
            stop.set()
            raise

    lost = None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(guarded, item) for item in todo]
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


def act_worker(client, system, domains, out, progress):
    def work(job):
        celex, sources = job
        record, failed = {"celex": celex}, False
        try:
            answer, title, log = classify_act(client, system, domains["ids"], celex, sources)
        except ConnectionLost:
            raise
        except Unclassified as exc:
            record["failure"], failed = str(exc), True
        except Exception as exc:  # noqa: BLE001 - one unreadable act must not stop the run
            record["failure"], failed = f"{type(exc).__name__}: {exc}"[:400], True
        else:
            record.update(answer, title=title, requests=log)
        if failed:
            print(f"Could not classify the act {celex}: {record['failure']}", flush=True)
        write_json(act_path(out, celex), record)
        progress.step(failed)
    return work


def provision_worker(client, system, domains, out, progress):
    def work(item):
        record = {key: item[key] for key in ("celex", "target", "kind", "target_id", "run", "level", "area")}
        failed = flagged = False
        try:
            saved = read_json(item["source"])
            pack = saved["evidence_pack"]
            record["title"] = squash(pack["document"].get("title"))
            record["text"] = squash(" ".join(target_text(pack["provision"])))[:600]
            act = read_act(out, item["celex"])
            classification, audit = classify(client, system, domains["ids"], pack, act)
        except ConnectionLost:
            raise
        except Unclassified as exc:
            record["failure"], failed = str(exc), True
        except Exception as exc:  # noqa: BLE001 - one unreadable provision must not stop the run
            record["failure"], failed = f"{type(exc).__name__}: {exc}"[:400], True
        else:
            record["classification"] = finish(classification, item, domains, act)
            record["audit"] = audit
            flagged = bool(record["classification"]["check"])
        if failed:
            print(f"Could not classify {item['celex']} {item['target']}: {record['failure']}", flush=True)
        write_json(record_path(out, item), record)
        progress.step(failed, flagged)
    return work


def run_until_done(client, name, open_items, make_work, workers, after_pass=lambda: None):
    """Run one step until every item was asked once, waiting through outages.

    open_items() returns (items still to ask, number already done). Items that Qwen answered badly are
    saved as failed and tried again the next time the command is started.
    """
    quick = 0
    while True:
        todo, done = open_items()
        if not todo:
            return
        progress = Progress(done + len(todo), done, name)
        progress.show()
        lost = run_pool(todo, make_work(progress), workers)
        after_pass()
        if lost is None:
            return
        if progress.done > done:
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


def act_links(celex):
    """The official EUR-Lex page and PDF of an act."""
    base = "https://eur-lex.europa.eu/legal-content/EN"
    return {"eurlex_url": f"{base}/TXT/?uri=CELEX:{celex}", "pdf_url": f"{base}/TXT/PDF/?uri=CELEX:{celex}"}


def with_coverage(record):
    """The saved classification, with a flag when only the first parts of a long provision were read."""
    result = dict(record["classification"])
    audit = record.get("audit", {})
    total, asked = audit.get("parts_total", 1), len({str(part.get("part", "")).split(".")[0]
                                                      for part in audit.get("parts", [])}) or 1
    if total > asked:
        result["parts_read"] = f"first {asked} of {total}"
        result["check"] = [*result["check"], "long_provision_partly_read"]
    return result


def safe_name(text):
    return re.sub(r"[^A-Za-z0-9ÄÖÜäöüß._-]+", "_", text).strip("_") or "department"


def export(out, items, domains):
    """Write results.json, summary.json, review.csv and one file per department."""
    results, rows, failures = {}, [], []
    primary, secondary, checks, levels = Counter(), Counter(), Counter(), Counter()
    inbox = defaultdict(lambda: defaultdict(lambda: {"title": "", "provisions": []}))
    for item in items:
        path = record_path(out, item)
        if not path.is_file():
            continue
        record = read_json(path)
        if record.get("classification") is None:
            failures.append({"celex": record["celex"], "target": record["target"], "error": record.get("failure")})
            continue
        result = with_coverage(record)
        results.setdefault(record["celex"], {})[record["target"]] = result
        primary[result["primary_domain"]] += 1
        levels[result["level"]] += 1
        if result["secondary_domain"]:
            secondary[result["secondary_domain"]] += 1
        checks.update(result["check"])
        rows.append([record["celex"], record["target"], result["level"], ", ".join(result["business_area"]),
                     result.get("act_domain") or "", result["subject"], result["primary_domain"],
                     result["primary_reason"],
                     result["secondary_domain"] or "", result["secondary_reason"], ", ".join(result["check"]),
                     record.get("text", "")[:400]])
        for role, domain, reason, departments in (
                ("primary", result["primary_domain"], result["primary_reason"], result["departments"]),
                ("secondary", result["secondary_domain"], result["secondary_reason"],
                 result["secondary_departments"])):
            if not domain:
                continue
            entry = {"provision": record["target"], "provision_id": record["target_id"],
                     "level": result["level"], "role": role, "domain": domain,
                     "subject": result["subject"], "reason": reason,
                     **({"to_confirm": result["to_confirm"]} if result.get("to_confirm") else {}),
                     **({"check": result["check"]} if result["check"] else {})}
            for department in departments or [NO_DEPARTMENT]:
                act = inbox[department][record["celex"]]
                act["title"] = record.get("title", "")
                act["provisions"].append(entry)
    write_json(out / "results.json", results)
    act_records, act_domains = {}, Counter()
    for celex in sorted({item["celex"] for item in items}):
        act = read_act(out, celex)
        if act and act.get("act_domain"):
            act_records[celex] = {key: act.get(key, "") for key in ("title", "act_domain", "act_subject", "reason")}
            act_domains[act["act_domain"]] += 1
    write_json(out / "acts.json", act_records)
    differs = sum(1 for provisions in results.values() for result in provisions.values()
                  if result.get("act_domain") in domains["ids"] and result["primary_domain"] != result["act_domain"])
    with (out / "review.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["celex", "provision", "level", "business_area", "act_domain", "subject", "primary_domain",
                         "primary_reason", "secondary_domain", "secondary_reason", "check", "text_start",
                         "primary correct (yes/no)", "right domain if wrong", "comment"])
        writer.writerows(rows)
    department_counts = {}
    for department, acts in sorted(inbox.items()):
        provisions = [p for act in acts.values() for p in act["provisions"]]
        department_counts[department] = {
            "acts": len(acts), "provisions": len(provisions),
            "direct": sum(p["level"] == "DIRECT" for p in provisions),
            "indirect": sum(p["level"] == "INDIRECT" for p in provisions),
        }
        write_json(out / "departments" / f"{safe_name(department)}.json", {
            "department": department, "counts": department_counts[department],
            "acts": [{"celex": celex, "title": act["title"], **act_links(celex), "provisions": act["provisions"]}
                     for celex, act in sorted(acts.items())],
        })
    decided = sum(primary.values())
    summary = {
        "domain_catalogue": domains["version"],
        "provisions_planned": len(items), "provisions_classified": decided, "provisions_failed": len(failures),
        "provisions_open": len(items) - decided - len(failures), "levels": dict(levels),
        "acts_classified": len(act_records), "act_domains": dict(act_domains.most_common()),
        "provisions_outside_their_act_domain": differs,
        "primary_domains": dict(primary.most_common()), "secondary_domains": dict(secondary.most_common()),
        "with_secondary_domain": sum(secondary.values()),
        "share_with_secondary_domain": round(sum(secondary.values()) / decided, 3) if decided else 0,
        "to_check": dict(checks), "departments": department_counts, "failures": failures,
    }
    write_json(out / "summary.json", summary)
    return summary


def reachable(client):
    """Ask the endpoint for its model list. This also sets the address the client sends requests to."""
    try:
        found = client.discover()
    except Exception as exc:  # noqa: BLE001 - any failure of the check means "not reachable yet"
        return False, str(exc)
    return bool(found.get("ok")), str(found.get("error") or "")


def wait_until_reachable(client, why):
    step = 0
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
    parser.add_argument("--unsure-dir", type=Path, default=DEFAULT_UNSURE,
                        help="Folder of the UNSURE pass (results.json).")
    parser.add_argument("--domains", type=Path, default=ROOT / "config/domains_en.yaml")
    parser.add_argument("--domains-de", type=Path, default=ROOT / "config/domains_de.yaml")
    parser.add_argument("--anchors", type=Path, default=ROOT / "config/business_anchors_en.yaml")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--levels", nargs="+", choices=KEPT, default=list(KEPT),
                        help="Classify only provisions of these relevance levels.")
    parser.add_argument("--sample", type=int, help="Classify only this many provisions (a fixed choice).")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--count-only", action="store_true", help="Count the provisions and stop.")
    args = parser.parse_args()
    out = args.output_dir.resolve()

    try:
        domains = load_domains(args.domains, args.domains_de, args.anchors)
        system = system_prompt(PROMPT.read_text(encoding="utf-8"), domains)
        act_system = system_prompt(ACT_PROMPT.read_text(encoding="utf-8"), domains)
        items, facts = collect([run.resolve() for run in args.runs], args.unsure_dir)
    except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 1
    print(f"{len(items):,} relevant provisions found: DIRECT {facts['DIRECT']:,}, INDIRECT {facts['INDIRECT']:,} "
          f"({facts['from_unsure_pass']:,} of them from the UNSURE pass). "
          f"{len(domains['ids'])} domains, catalogue {domains['version']}.", flush=True)
    if facts["unsure_without_decision"]:
        print(f"Note: {facts['unsure_without_decision']:,} UNSURE provisions have no decision from the UNSURE "
              f"pass in {args.unsure_dir} and are left out.", flush=True)
    sources = defaultdict(list)          # every kept provision of an act helps to describe the act
    for item in items:
        sources[item["celex"]].append(item["source"])
    items = [item for item in items if item["level"] in args.levels]
    if args.count_only:
        return 0
    if not items:
        print("Nothing to classify.", file=sys.stderr)
        return 1

    fingerprint = sha([system, TASK, reply_schema(domains["ids"]), MAX_PARTS,
                       act_system, ACT_TASK, act_schema(domains["ids"]), ACT_PROVISIONS, ACT_OPENING_CHARS])
    config = out / "run_config.json"
    if config.is_file() and read_json(config).get("fingerprint") != fingerprint:
        print("The prompt or the domain files changed since this folder was started. "
              "Use a new --output-dir, for example runs/eurlex_classification_v6.", file=sys.stderr)
        return 1
    write_json(config, {"fingerprint": fingerprint, "domain_catalogue": domains["version"],
                        "runs": [str(run) for run in args.runs], "system_prompt": system,
                        "act_system_prompt": act_system})
    planned = sample(items, args.sample) if args.sample else items
    print(f"Planned: {len(planned):,} provisions" + (" (sample)." if args.sample else "."), flush=True)

    client = QwenClient.from_env(args.env_file, retries=1)
    workers = max(1, min(args.workers, MAX_WORKERS))
    planned_acts = sorted({item["celex"] for item in planned})

    def open_acts():
        todo = [(celex, sources[celex]) for celex in planned_acts
                if not (read_act(out, celex) or {}).get("act_domain")]
        return todo, len(planned_acts) - len(todo)

    def open_provisions():
        todo = []
        for item in planned:
            path = record_path(out, item)
            if not (path.is_file() and "classification" in read_json(path)):
                todo.append(item)  # New, or failed before: failed ones get one more try per start.
        return todo, len(planned) - len(todo)

    try:
        # The client learns the address and model name from the endpoint before the first request.
        ok, error = reachable(client)
        if not ok:
            wait_until_reachable(client, error)
        run_until_done(client, "Acts", open_acts,
                       lambda progress: act_worker(client, act_system, domains, out, progress), workers)
        run_until_done(client, "Provisions", open_provisions,
                       lambda progress: provision_worker(client, system, domains, out, progress), workers,
                       after_pass=lambda: export(out, planned, domains))
    except KeyboardInterrupt:
        export(out, planned, domains)
        print("\nStopped by Ctrl+C. Run the same command to continue.", flush=True)
        return 130

    summary = export(out, planned, domains)
    top = ", ".join(f"{name} {count:,}" for name, count in list(summary["primary_domains"].items())[:5])
    print(f"\nFinished: {summary['provisions_classified']:,} of {len(planned):,} classified, "
          f"{summary['provisions_failed']:,} failed. With a secondary domain: "
          f"{summary['with_secondary_domain']:,} ({summary['share_with_secondary_domain']:.0%}). "
          f"To check: {sum(summary['to_check'].values()):,}.")
    print(f"Largest domains: {top}")
    print(f"Results: {out / 'results.json'}\nTo check by hand: {out / 'review.csv'}\n"
          f"Per department: {out / 'departments'}")
    if summary["provisions_failed"]:
        print("Run the same command again to retry the failed ones.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
