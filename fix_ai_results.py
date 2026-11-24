"""Correct the AI results where the inspection finds known mistakes. Asks Qwen only for those items.

Steps (each item is asked once; the same command continues after Ctrl+C):

0. changes   the "Was hat sich geändert" sentences of the changed articles: the repair of
             summarize_changed_provisions.py --redo-sentences (one-sided wording, number marks, English
             headlines), if it was not run yet.
1. recheck   provisions marked DIRECT whose rule binds only an authority, and kept provisions that look
             formal: Qwen answers who must act (BUSINESS / AUTHORITY_ONLY / DATES_ONLY / FORMAL_ONLY).
             AUTHORITY_ONLY and DATES_ONLY: DIRECT becomes INDIRECT. FORMAL_ONLY: IGNORE.
2. point     summary points (v1 and v2) that say "muss" where the text says "may", or that state an
             authority's duty without naming the authority: Qwen checks the point against its source
             sentences and corrects only that.
   verify    every correction of step 2 is checked once more: Qwen copies the sentence of the legal text the
             point describes and chooses the original or the correction. A correction is used only when it
             is chosen and the copied sentence is in the text and supports it ("may" for a permission).
             Corrections that only name who must act are not used: in the hand check about half of them named
             the wrong actor or broke the sentence, while the original point (passive) is not wrong. Acts whose
             summaries were written with a correction that is not used are written again.
3. language  summaries (v1 and v2) whose headline or overview is English: translated into German.
Without Qwen:
             an amending article gets the note "Ändert <act>"; an annex summary that starts with "Die
             Verordnung ..." starts with "Der Anhang ...".
4. acts      the act summaries written again from the corrected provision summaries, for every act that
             the steps above changed, every act with changed articles (their summaries from today's text
             replace version 1, deleted articles leave, articles inserted later join) and every act whose
             department summary failed.
5. departments  the department summaries of those acts written again in the same way.
6. wording   points of department summaries (version 1 and new) that are English (mostly the open questions
             "Does Max Bögl ...?"): translated into German.

A correction that brings in a number, date or unit that is not in the source is not used. Nothing in
the run folders is changed: all corrections go to one file that the database import applies on top.

    python fix_ai_results.py --count-only      # how many items, no Qwen
    python fix_ai_results.py                   # everything (continues)

Output (runs/eurlex_fixes_v1): corrections.json (for the import), review.csv (every change, to check by
hand), summary.json, answers/ (one saved answer per item), acts/ and departments/ (the summaries written
again; for an act listed in corrections.json "acts_redone" they replace all its version-1 act and department
summaries).
"""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from classify_eurlex_provisions import (  # noqa: E402
    MAX_WORKERS, ConnectionLost, Unclassified, reachable, read_json, run_until_done, safe_name, sha, squash,
    target_text, wait_until_reachable, write_json)
from classify_eurlex_provisions import NO_DEPARTMENT  # noqa: E402
import inspect_ai_results as check  # noqa: E402
from lr_ai.qwen_client import QwenClient  # noqa: E402
import summarize_changed_provisions as changes  # noqa: E402
import summarize_eurlex_provisions as base  # noqa: E402

VERSION = "fixes-v1"
OUTPUT = ROOT / "runs/eurlex_fixes_v1"
PROMPTS = {"recheck": ROOT / "prompts/eurlex_provision_recheck.md",
           "point": ROOT / "prompts/eurlex_point_check.md",
           "verify": ROOT / "prompts/eurlex_point_verify.md",
           "language": ROOT / "prompts/eurlex_summary_translate.md"}
TASKS = {"recheck": "TASK: Answer who must or may act under this provision. Return the JSON object described in "
                    "the instructions.",
         "point": "TASK: Check the point against the source. Return the JSON object described in the instructions.",
         "verify": "TASK: Copy the sentence the point describes and choose A, B or NEITHER. Return the JSON object "
                   "described in the instructions.",
         "language": "TASK: Translate the headline and the overview into German. Return the JSON object described "
                     "in the instructions."}
WHO = ("BUSINESS", "AUTHORITY_ONLY", "DATES_ONLY", "FORMAL_ONLY")
PROBLEMS = ("NONE", "PERMISSION_AS_DUTY", "ACTOR_MISSING", "BOTH")
SCHEMAS = {
    "recheck": {"type": "object", "additionalProperties": False, "required": ["who_must_act", "reason"],
                "properties": {"who_must_act": {"type": "string", "enum": list(WHO)},
                               "reason": {"type": "string"}}},
    "point": {"type": "object", "additionalProperties": False, "required": ["problem", "point"],
              "properties": {"problem": {"type": "string", "enum": list(PROBLEMS)}, "point": {"type": "string"}}},
    "verify": {"type": "object", "additionalProperties": False, "required": ["evidence", "choice"],
               "properties": {"evidence": {"type": "string"},
                              "choice": {"type": "string", "enum": ["A", "B", "NEITHER"]}}},
    "language": {"type": "object", "additionalProperties": False, "required": ["headline", "overview"],
                 "properties": {"headline": {"type": "string"}, "overview": {"type": "string"}}},
}
MAX_TOKENS = {"recheck": 500, "point": 700, "verify": 700, "language": 1200}
MAX_TEXT = 6000
MAX_SOURCE = 2500
STEPS = ("changes", "recheck", "point", "verify", "language", "acts", "departments", "wording")
QWEN_STEPS = ("recheck", "point", "verify", "language")
ACT_FIX = "act-fix-v1"
ACTOR_FIXES_USED = False   # see "verify" above
WORDING_SYSTEM = ("You translate one point of a German department summary of an EU legal act into German. Keep "
                  "names, numbers, standards, codes, references and the names of parts (for example 'Annex I, "
                  "1.1. Requirements ...') exactly as they are. A question stays a question. Use precise German "
                  "legal terms. Return one JSON object with text.")
WORDING_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["text"],
                  "properties": {"text": {"type": "string"}}}

AMENDED_ACT = re.compile(
    r"((?:Implementing |Delegated )?(?:Regulation|Directive|Decision)(?: \((?:EU|EC|EEC|EU, Euratom|Euratom)\))?"
    r" (?:No )?\d{1,4}/\d{2,4}(?:/(?:EC|EEC|EU|ECSC|Euratom))?)")
ACT_WORDS = [("Implementing Regulation", "Durchführungsverordnung"), ("Delegated Regulation", "Delegierte Verordnung"),
             ("Implementing Decision", "Durchführungsbeschluss"), ("Regulation", "Verordnung"),
             ("Directive", "Richtlinie"), ("Decision", "Beschluss"), ("(EC)", "(EG)"), ("(EEC)", "(EWG)"),
             ("No ", "Nr. "), ("/EC", "/EG"), ("/EEC", "/EWG")]


# ------------------------------------------------------------------ without Qwen

def own_numbers(celex):
    """'2017/2400' and '2400/2017' for 32017R2400: how an act names itself."""
    match = re.match(r"\d(\d{4})[A-Z]{1,2}0*(\d+)", celex or "")
    return {f"{match.group(1)}/{match.group(2)}", f"{match.group(2)}/{match.group(1)}"} if match else set()


def amended_act(text, celex=""):
    """'Ändert Verordnung (EU) 2017/1151' for an amending article or annex, else ''. Never the act itself."""
    start = (text or "")[:400]
    if not check.AMENDING.search(start):
        return ""
    own = own_numbers(celex)
    match = next((m for m in AMENDED_ACT.finditer(start)
                  if not any(number in m.group(1) for number in own)), None)
    if match is None:
        return ""
    name = match.group(1)
    for english, german in ACT_WORDS:
        name = name.replace(english, german)
    return f"Ändert {name}"


ACT_NUMBER_AFTER = re.compile(r"\s*(?:\((?:EU|EG|EWG|EU, Euratom|Euratom)\)\s*)?(?:Nr\.\s*)?\d{1,4}/\d{2,4}"
                              r"(?:/(?:EU|EG|EWG|EGKS|Euratom))?")


def annex_subject(overview):
    """'Der Anhang regelt ...' instead of 'Die Verordnung regelt ...' (also 'Die Verordnung (EG) Nr. 595/2009
    regelt ...') at the start of an annex summary."""
    overview = overview or ""
    match = check.ANNEX_AS_ACT.search(overview)
    if not match:
        return overview
    rest = overview[match.end():]
    number = ACT_NUMBER_AFTER.match(rest)
    return "Der Anhang" + (rest[number.end():] if number else rest)


def source_sentences(quotes, text):
    """The sentences of the legal text that hold the quotes (the quote itself when it is not found)."""
    flat = re.sub(r"\s+", " ", text or "")
    pieces = []
    for quote in quotes or []:
        if not isinstance(quote, str) or not quote.strip():
            continue
        wanted = re.sub(r"\s+", " ", quote).strip()
        position = flat.find(wanted[:80]) if flat else -1
        if position < 0:
            piece = wanted
        else:
            start = flat.rfind(". ", max(0, position - 400), position)
            start = start + 2 if start >= 0 else max(0, position - 400)
            end = flat.find(". ", position + len(wanted), position + len(wanted) + 400)
            end = end + 1 if end >= 0 else min(len(flat), position + len(wanted) + 400)
            piece = flat[start:end].strip()
        if piece and piece not in pieces:
            pieces.append(piece)
    joined, out = 0, []
    for piece in pieces:
        if joined + len(piece) > MAX_SOURCE and out:
            break
        out.append(piece[:MAX_SOURCE])
        joined += len(piece)
    return out


# ------------------------------------------------------------------ choosing the items

def provision_text(path):
    try:
        pack = read_json(path)
    except (OSError, ValueError):
        return None, "", ""
    evidence = pack.get("evidence_pack", {})
    text = " ".join(target_text(evidence.get("provision", {})))
    return text, (evidence.get("document") or {}).get("title") or "", (pack.get("details") or {}).get("actor") or ""


def recheck_items(provisions):
    items = []
    for (celex, target), found in sorted(provisions.items()):
        if found["level"] not in check.KEPT:
            continue
        text, title, actor = provision_text(found["file"])
        if text is None:
            continue
        flags = []
        if found["level"] == "DIRECT" and check.authority_kind(actor, text) == "authority":
            flags.append("authority")
        if len(text) < 1500 and check.FORMAL.search(text):
            flags.append("formal")
        if flags:
            items.append({"step": "recheck", "id": sha([celex, target])[:24], "celex": celex, "target": target,
                          "level": found["level"], "flags": flags, "title": title, "text": text[:MAX_TEXT]})
    return items


FORMAL_REASON = re.compile(r"addressed to|repeal|correlation|committee|delegat", re.IGNORECASE)


def second_round(out, items):
    """Recheck answers AUTHORITY_ONLY whose reason shows a formal clause (addressees, repeal, committee or
    delegation procedure) are asked once more with the sharpened instructions, under a new id."""
    for item in items:
        if item["step"] != "recheck" or item.get("round") == 2:
            continue
        saved = saved_answer(out, item)
        answer = (saved or {}).get("answer") or {}
        if answer.get("who_must_act") == "AUTHORITY_ONLY" and FORMAL_REASON.search(answer.get("reason", "")):
            item["id"], item["round"] = f"{item['id']}-2", 2
    return items


def summary_items(version, celex, target, summary, start, full_text, items, fixes, name=None):
    """Point and language items of one summary, and the fixes that need no Qwen. target: the key of the
    summary (v1: the provision, v2: the article/annex key); name: its name ("Annex I"); start: the beginning
    of the provision's text; full_text(): the whole text, read only when a point needs its source sentences."""
    name = name or target
    headline, overview = summary.get("headline") or "", summary.get("overview") or ""
    text = None
    for index, bullet in enumerate(summary.get("bullets") or []):
        problems = check.point_problems(bullet)
        if problems:
            text = full_text() if text is None else text
            items.append({"step": "point", "id": sha([version, celex, target, index])[:24], "version": version,
                          "celex": celex, "target": target, "name": name, "index": index,
                          "kind": bullet.get("kind", ""),
                          "point": bullet.get("text") or "", "flags": sorted(problems),
                          "source": source_sentences(bullet.get("quotes"), text)})
    if check.english(headline) or check.english(overview):
        items.append({"step": "language", "id": sha([version, celex, target, "language"])[:24], "version": version,
                      "celex": celex, "target": target, "name": name, "headline": headline, "overview": overview})
    note = amended_act(start, celex) if not check.AMENDED_DE.search(f"{headline} {overview}") else ""
    if note:
        fixes[(version, celex, target)]["amends"] = note
    if name.startswith("Annex") and check.ANNEX_AS_ACT.search(overview.strip()):
        fixes[(version, celex, target)]["annex_subject"] = True


def v1_items(folder, files):
    items, fixes = [], defaultdict(dict)
    for path in sorted((folder / "provisions" / "targets").glob("*/*.json")):
        record = check.read_or_none(path)
        if not record or not isinstance(record.get("summary"), dict):
            continue
        celex, target = record.get("celex") or "", record.get("target") or ""
        start = record.get("text") or ""

        file = files.get((celex, target))

        def full_text(file=file, start=start):
            return (provision_text(file)[0] if file else None) or start

        summary_items("v1", celex, target, record["summary"], start, full_text, items, fixes)
    return items, fixes


def v2_items(folder):
    items, fixes, acts = [], defaultdict(dict), {}
    config = check.read_or_none(folder / "run_config.json") or {}
    changed = Path(config.get("changed_dir") or "")
    for path in sorted((folder / "units").glob("*/*.json")):
        record = check.read_or_none(path)
        if not record or not isinstance(record.get("summary"), dict):
            continue
        celex, key = record.get("celex") or "", record.get("key") or ""
        if celex not in acts:
            acts[celex] = check.read_or_none(changed / "acts" / f"{celex}.json") or {}
        text = (((acts[celex].get("roots") or {}).get(key) or {}).get("today") or {}).get("text") or ""
        summary_items("v2", celex, key, record["summary"], text[:600], lambda text=text: text, items, fixes,
                      record.get("name") or key)
    return items, fixes


# ------------------------------------------------------------------ the questions

def ask_recheck(client, system, item, log):
    sent = {"act": {"celex": item["celex"], "title": item["title"]}, "provision": item["target"], "text": item["text"]}

    def validate(reply):
        if not isinstance(reply, dict) or reply.get("who_must_act") not in WHO:
            raise Unclassified(f"who_must_act must be one of {', '.join(WHO)}.")
        return {"who_must_act": reply["who_must_act"], "reason": squash(reply.get("reason") or "")}

    return base.ask(client, system, sent, TASKS["recheck"], SCHEMAS["recheck"], "recheck", validate, log,
                    MAX_TOKENS["recheck"])


def ask_point(client, system, item, log):
    sent = {"provision": item.get("name") or item["target"], "kind": item["kind"], "point": item["point"],
            "source": item["source"]}
    source = " ".join([item["point"], *item["source"]])

    def validate(reply, final=False):
        if not isinstance(reply, dict) or reply.get("problem") not in PROBLEMS:
            raise Unclassified(f"problem must be one of {', '.join(PROBLEMS)}.")
        point = squash(reply.get("point") or "")
        if reply["problem"] == "NONE" or not point:
            return {"problem": "NONE", "point": item["point"]}
        marks = base.number_marks([point], source)
        if marks and not final:
            raise Unclassified(" ".join(marks) + " Keep the numbers of the point and the source exactly.")
        if marks:
            return {"problem": reply["problem"], "point": item["point"], "not_used": "; ".join(marks)}
        return {"problem": reply["problem"], "point": point}

    return base.ask(client, system, sent, TASKS["point"], SCHEMAS["point"], "point_check", validate, log,
                    MAX_TOKENS["point"])


PERMISSION_EN = re.compile(r"\b(?:may|can|is allowed to|are allowed to|is permitted to|are permitted to|"
                           r"at (?:its|their) discretion|optional(?:ly)?)\b", re.IGNORECASE)
DUTY_EN = re.compile(r"\b(?:shall|must|is required to|are required to|is obliged to|are obliged to)\b",
                     re.IGNORECASE)


def plain_words(text):
    return re.sub(r"[^\w%]+", " ", (text or "").casefold()).strip()


def supports(evidence, problem, source):
    """Whether the copied sentence is in the source and supports the correction: a permission for
    PERMISSION_AS_DUTY ("may" before any "shall"), an authority for ACTOR_MISSING, both for BOTH."""
    words = plain_words(evidence)
    if len(words) < 20 or words[:120] not in plain_words(" ".join(source)):
        return False, "the copied sentence is not in the legal text"
    permission = PERMISSION_EN.search(evidence)
    duty = DUTY_EN.search(evidence)
    may_ok = bool(permission) and (not duty or permission.start() < duty.start())
    actor_ok = bool(check.AUTHORITY.search(evidence))
    needed = {"PERMISSION_AS_DUTY": may_ok, "ACTOR_MISSING": actor_ok, "BOTH": may_ok and actor_ok}
    if not needed.get(problem, False):
        return False, ("the sentence states a duty" if problem != "ACTOR_MISSING" and duty and not may_ok
                       else "the sentence does not support the correction")
    return True, ""


def ask_verify(client, system, item, log):
    sent = {"provision": item.get("name") or item["target"], "problem": item["problem"], "source": item["source"],
            "point_a": item["point"], "point_b": item["proposed"]}

    def validate(reply):
        if not isinstance(reply, dict) or reply.get("choice") not in ("A", "B", "NEITHER"):
            raise Unclassified("choice must be A, B or NEITHER.")
        evidence = squash(reply.get("evidence") or "")
        answer = {"choice": reply["choice"], "evidence": evidence}
        if reply["choice"] == "B":
            ok, why = supports(evidence, item["problem"], item["source"])
            answer["used"] = ok
            if not ok:
                answer["why_not"] = why
        else:
            answer["used"] = False
        return answer

    return base.ask(client, system, sent, TASKS["verify"], SCHEMAS["verify"], "point_verify", validate, log,
                    MAX_TOKENS["verify"])


def verify_items(out, items):
    """One item per correction of step 2 (a point that Qwen changed)."""
    result = []
    for item in items:
        if item["step"] != "point":
            continue
        saved = saved_answer(out, item)
        answer = (saved or {}).get("answer") or {}
        if answer.get("problem") in (None, "NONE") or answer.get("not_used") or answer.get("point") == item["point"]:
            continue
        result.append({**item, "step": "verify", "problem": answer["problem"], "proposed": answer["point"]})
    return result


def ask_language(client, system, item, log):
    name = item.get("name") or item["target"]
    sent = {"provision": name, "headline": item["headline"], "overview": item["overview"]}
    source = f"{name} {item['headline']} {item['overview']}"

    def validate(reply, final=False):
        if not isinstance(reply, dict):
            raise Unclassified("Return headline and overview.")
        headline, overview = squash(reply.get("headline") or ""), squash(reply.get("overview") or "")
        if not headline or (item["overview"] and not overview):
            raise Unclassified("Return the headline and the overview, both in German.")
        problem = base.number_marks([headline, overview], source)
        if check.english(headline) or check.english(overview):
            problem.append("The text is still English. Write it in German.")
        if problem and not final:
            raise Unclassified(" ".join(problem))
        if problem:
            return {"headline": item["headline"], "overview": item["overview"], "not_used": "; ".join(problem)}
        return {"headline": headline, "overview": overview or item["overview"]}

    return base.ask(client, system, sent, TASKS["language"], SCHEMAS["language"], "translation", validate, log,
                    MAX_TOKENS["language"])


def ask_wording(client, system, item, log):
    def validate(reply, final=False):
        text = squash((reply or {}).get("text") if isinstance(reply, dict) else "")
        if not text:
            raise Unclassified("Return the text.")
        problem = base.number_marks([text], item["text"])
        if english_point(text):
            problem.append("The text is still English. Write it in German.")
        if problem and not final:
            raise Unclassified(" ".join(problem))
        return {"text": item["text"], "not_used": "; ".join(problem)} if problem else {"text": text}

    return base.ask(client, WORDING_SYSTEM, {"text": item["text"]}, "TASK: Translate the point into German.",
                    WORDING_SCHEMA, "wording", validate, log, 600)


def wording_items(out, v1_folder, redone):
    """English points of the department summaries in use: the new ones, and version 1 of acts not redone."""
    items = []
    folders = [("fix", out / "departments"), ("v1", v1_folder / "departments")]
    for origin, folder in folders:
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            record = check.read_or_none(path) or {}
            celex = record.get("celex") or ""
            key = path.stem[len(safe_name(celex)) + 1:] if celex else ""
            in_use = key in redone.get(celex, ()) if origin == "fix" else celex not in redone
            if not celex or not in_use:
                continue
            items += english_points(origin, path, record)
    return items


ENGLISH_QUESTION = re.compile(r"^(?:Does|Do|Is|Are|Has|Have|Can|Will|Should|What|Which|Who|How)\s")


def english_point(text):
    """English text, also a short English question ("Does Max Bögl operate heat pumps?")."""
    return bool(text) and (check.english(text) or bool(ENGLISH_QUESTION.match(text)))


def english_points(origin, path, record):
    """One item per English point of one department summary."""
    return [{"step": "wording", "id": sha([origin, path.stem, index, bullet["text"]])[:24], "celex": record["celex"],
             "target": path.stem, "origin": origin, "file": path.stem, "index": index, "text": bullet["text"]}
            for index, bullet in enumerate(record.get("bullets") or [])
            if isinstance(bullet, dict) and english_point(bullet.get("text"))]


ASK = {"recheck": ask_recheck, "point": ask_point, "verify": ask_verify, "language": ask_language,
       "wording": ask_wording}


# ------------------------------------------------------------------ corrections

NEW_LEVEL = {"AUTHORITY_ONLY": {"DIRECT": "INDIRECT"}, "DATES_ONLY": {"DIRECT": "INDIRECT"},
             "FORMAL_ONLY": {"DIRECT": "IGNORE", "INDIRECT": "IGNORE"}}


def answer_path(out, item):
    return out / "answers" / item["step"] / f"{item['id']}.json"


def saved_answer(out, item):
    record = check.read_or_none(answer_path(out, item))
    return record if record and record.get("version") == VERSION and "answer" in record else None


def corrections(out, items, fixes, legacy=None):
    """corrections.json and review.csv from the saved answers, the fixes without Qwen and the act and
    department summaries written again."""
    result = {"version": VERSION, "made": f"{datetime.now():%Y-%m-%d %H:%M}", "provision_levels": {},
              "v1": {}, "v2": {}}
    rows, counts, rejected = [], Counter(), set()
    legacy = legacy if legacy is not None else legacy_bases(out)

    def entry(version, celex, target):
        return result[version].setdefault(celex, {}).setdefault(target, {})

    for item in items:
        if item["step"] == "verify":
            continue
        if item["step"] == "wording":
            saved = saved_answer(out, item)
            if saved and saved["answer"]["text"] != item["text"]:
                saved["answer"]["text"] = changes.annex_or_appendix(saved["answer"]["text"], item["text"])
                result.setdefault("department_points", {}).setdefault(f"{item['origin']}/{item['file']}", {})[
                    str(item["index"])] = saved["answer"]["text"]
                counts["department points translated"] += 1
                rows.append(["wording", item["celex"], item["file"], f"point {item['index'] + 1}", item["text"],
                             saved["answer"]["text"], item["origin"]])
            continue
        saved = saved_answer(out, item)
        if saved is None:
            counts[f"{item['step']}: not done"] += 1
            continue
        answer = saved["answer"]
        if item["step"] == "recheck":
            after = NEW_LEVEL.get(answer["who_must_act"], {}).get(item["level"], item["level"])
            counts[f"recheck: {answer['who_must_act']}"] += 1
            if after != item["level"]:
                result["provision_levels"].setdefault(item["celex"], {})[item["target"]] = {
                    "before": item["level"], "after": after, "who_must_act": answer["who_must_act"],
                    "reason": answer["reason"]}
                counts[f"level {item['level']} -> {after}"] += 1
                rows.append(["recheck", item["celex"], item["target"], "level", item["level"], after,
                             f"{answer['who_must_act']}: {answer['reason']}"])
        elif item["step"] == "point":
            counts[f"point: {answer['problem']}" + (" (not used)" if answer.get("not_used") else "")] += 1
            verified = saved_answer(out, {**item, "step": "verify"})
            if answer["point"] != item["point"]:
                used = bool(verified and verified["answer"].get("used"))
                actor_only = answer["problem"] in ("ACTOR_MISSING", "BOTH") and not ACTOR_FIXES_USED
                if actor_only:
                    used = False
                counts[f"point correction {'used' if used else 'not used'} after the check"] += 1
                if not used:
                    rejected.add(item["celex"])
                    why = ("corrections that only name who acts are not used" if actor_only else
                           verified["answer"].get("why_not") or f"check chose {verified['answer']['choice']}"
                           if verified else "not checked")
                    rows.append([f"point {item['version']} (not used)", item["celex"], item["target"],
                                 f"point {item['index'] + 1}", item["point"], answer["point"],
                                 f"{answer['problem']}: {why}"])
                    continue
                entry(item["version"], item["celex"], item["target"]).setdefault("bullets", {})[str(item["index"])] = {
                    "before": item["point"], "after": answer["point"], "problem": answer["problem"]}
                rows.append([f"point {item['version']}", item["celex"], item["target"], f"point {item['index'] + 1}",
                             item["point"], answer["point"], answer["problem"]])
        else:
            changed = {key: answer[key] for key in ("headline", "overview") if answer[key] != item[key]}
            counts["language: translated" if changed else "language: not used"] += 1
            if changed:
                entry(item["version"], item["celex"], item["target"]).update(changed)
                for key, value in changed.items():
                    rows.append([f"language {item['version']}", item["celex"], item["target"], key, item[key], value,
                                 ""])
    for (version, celex, target), fix in sorted(fixes.items()):
        record = entry(version, celex, target)
        if fix.get("amends"):
            record["amends"] = fix["amends"]
            counts[f"amends note {version}"] += 1
            rows.append([f"amends {version}", celex, target, "note", "", fix["amends"], "without Qwen"])
        if fix.get("annex_subject"):
            record["overview_starts_with_annex"] = True
            counts[f"annex start {version}"] += 1
            rows.append([f"annex start {version}", celex, target, "overview", "Die Verordnung …", "Der Anhang …",
                         "without Qwen"])
    for version in ("v1", "v2"):
        result[version] = {celex: targets for celex, targets in result[version].items() if targets}
    result["rejected_acts"] = sorted(rejected)
    redone = {}
    for path in sorted((out / "acts").glob("*.json")) if (out / "acts").is_dir() else []:
        record = check.read_or_none(path)
        if not record or record.get("version") != ACT_FIX or "sentences" not in record \
                or path.name != act_file(out, record.get("celex", "")).name:   # an older file name
            continue
        basis = act_basis(result, record["celex"])
        legacy_ok = legacy.get(record["celex"]) == basis
        if not current(record, basis, legacy_ok):
            counts["older act summaries not used (their corrections changed)"] += 1
            continue
        keys = record.get("department_keys", [])
        done = [key for key in keys if saved_department(out, record["celex"], key, basis, legacy_ok)]
        redone[record["celex"]] = {"provisions": record.get("provisions", 0), "departments": done,
                                   "departments_open": len(keys) - len(done), "facts": record.get("facts", {})}
        counts["acts written again"] += 1
        counts["department summaries written again"] += len(done)
        counts["acts without a relevant provision now"] += not record.get("provisions")
    result["acts_redone"] = redone
    write_json(out / "corrections.json", result)
    with (out / "review.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["step", "celex", "provision", "field", "before", "after", "reason",
                         "correct (yes/no)", "comment"])
        writer.writerows(rows)
    summary = dict(sorted(counts.items()))
    write_json(out / "summary.json", summary)
    return summary


# ------------------------------------------------------------------ the corrected provisions of an act

def fixed_summary(summary, fix):
    """A summary with its corrections applied (headline, overview, points, annex start, amends note)."""
    summary = json.loads(json.dumps(summary))
    fix = fix or {}
    for key in ("headline", "overview"):
        if fix.get(key):
            summary[key] = fix[key]
    for index, change in (fix.get("bullets") or {}).items():
        bullets = summary.get("bullets") or []
        if int(index) < len(bullets):
            bullets[int(index)]["text"] = change["after"]
    if fix.get("overview_starts_with_annex"):
        summary["overview"] = annex_subject(summary.get("overview"))
    if fix.get("amends"):
        summary["amends"] = fix["amends"]
    return summary


def act_records(celex, corrections, v1_folder, v2_folder, extra):
    """The provisions of one act as the act and department summaries see them after the corrections:
    the new levels (IGNORE leaves), the corrected summaries, the summaries from today's text for changed
    articles, without deleted articles, with the relevant articles inserted later. Returns (records, facts)."""
    levels = corrections["provision_levels"].get(celex, {})
    fixes1, fixes2 = corrections["v1"].get(celex, {}), corrections["v2"].get(celex, {})
    today, deleted, inserted = {}, set(), {}
    for path in sorted((v2_folder / "units" / safe_name(celex)).glob("*.json")):
        unit = check.read_or_none(path)
        if not unit or unit.get("failure"):
            continue
        if unit.get("kind") == "deleted":
            deleted.update(p["locator"] for p in unit.get("provisions", []))
        elif unit.get("kind") == "summary" and isinstance(unit.get("summary"), dict):
            summary = fixed_summary(unit["summary"], fixes2.get(unit.get("key")))
            for provision in unit.get("provisions", []):
                today[provision["locator"]] = summary
                if provision.get("inserted_later"):
                    inserted[provision["locator"]] = (provision, unit)
    records, facts = [], Counter()
    for path in sorted((v1_folder / "provisions" / "targets" / celex).glob("*.json")):
        record = check.read_or_none(path)
        if not record or not isinstance(record.get("summary"), dict) or base.is_other_language_row(record):
            continue
        target = record["target"]
        level = (levels.get(target) or {}).get("after", record["level"])
        if level == "IGNORE" or target in deleted:
            facts["left out (IGNORE or deleted)"] += 1
            continue
        record["level"] = level
        if target in today:
            record["summary"], record["summary_from"] = today[target], "today"
            facts["summary from today's text"] += 1
        else:
            record["summary"] = base.tidy_summary(fixed_summary(record["summary"], fixes1.get(target)),
                                                  record.get("text", ""), "de")
        records.append(record)
    present = {record["target"] for record in records}
    for locator, (provision, unit) in sorted(inserted.items()):
        result = (extra["classified"].get(celex) or {}).get(locator)
        found = extra["provisions"].get((celex, locator))
        if locator in present or not result or not found:
            continue
        level = (levels.get(locator) or {}).get("after", result.get("level") or provision.get("level"))
        if level not in check.KEPT:
            continue
        pack = check.read_or_none(found["file"]) or {}
        decision = found.get("resolved") or pack
        details = pack.get("details") or {}
        path = (found.get("resolved") or {}).get("indirect_path") or details.get("indirect_path")
        records.append({
            "celex": celex, "target": locator, "kind": found.get("kind") or "ARTICLE",
            "target_id": found.get("target_id") or f"{celex}:{locator}", "level": level,
            "title": unit.get("title", ""), "text": "", "classification": result, "summary": today[locator],
            "summary_from": "today, inserted later",
            "facts": {"why_relevant": squash(decision.get("reason")),
                      "indirect_path": path if path and path != "NONE" and level == "INDIRECT" else None,
                      "to_confirm": squash(result.get("to_confirm") or (found.get("resolved") or {}).get("to_confirm"))}})
        facts["inserted later, added"] += 1
    return records, facts


def affected_acts(corrections, v1_folder, v2_folder):
    """The acts whose act and department summaries are written again."""
    acts = set(corrections["provision_levels"])
    acts |= {celex for celex, targets in corrections["v1"].items()
             if any(set(fix) & {"headline", "overview", "bullets"} for fix in targets.values())}
    if (v2_folder / "units").is_dir():
        for folder in (v2_folder / "units").iterdir():
            unit = next((check.read_or_none(path) for path in sorted(folder.glob("*.json"))), None) \
                if folder.is_dir() else None
            if unit and unit.get("celex"):   # the folder name is the CELEX number made safe, "(01)" -> "_01"
                acts.add(unit["celex"])
    for path in (v1_folder / "departments").glob("*.json") if (v1_folder / "departments").is_dir() else []:
        record = check.read_or_none(path) or {}
        if record.get("failure") and record.get("celex"):
            acts.add(record["celex"])
    return sorted(acts)


def act_basis(corrections, celex):
    """A fingerprint of the corrections an act summary was written from."""
    return sha([corrections["provision_levels"].get(celex, {}), corrections["v1"].get(celex, {}),
                corrections["v2"].get(celex, {})])[:16]


def current(record, basis, legacy_ok):
    """A saved act or department summary is still right when it was written from the same corrections. A
    summary saved before the fingerprint existed is right when the corrections of its act are the same as
    then (legacy_ok)."""
    return record.get("basis") == basis if "basis" in record else legacy_ok


BEFORE = "corrections_first_run.json"


def legacy_bases(out):
    """{celex: basis} of the corrections the first act and department summaries were written from (saved
    once, before the first run that knows fingerprints rewrites corrections.json)."""
    first = out / BEFORE
    if not first.is_file() and (out / "corrections.json").is_file():
        first.write_bytes((out / "corrections.json").read_bytes())
    old = check.read_or_none(first) or {}
    if not old:
        return {}
    old.setdefault("provision_levels", {})
    old.setdefault("v1", {})
    old.setdefault("v2", {})
    return {celex: act_basis(old, celex) for celex in set(old["provision_levels"]) | set(old["v1"]) | set(old["v2"])
            | set(old.get("acts_redone", {}))}


def act_file(out, celex):
    return out / "acts" / f"{safe_name(celex)}.json"


def saved_act(out, celex, basis=None, legacy_ok=True):
    record = check.read_or_none(act_file(out, celex))
    if not record or record.get("version") != ACT_FIX or "sentences" not in record or record.get("celex") != celex:
        return None
    return record if basis is None or current(record, basis, legacy_ok) else None


def department_file(out, celex, key):
    return out / "departments" / f"{safe_name(celex)}_{key}.json"


def saved_department(out, celex, key, basis=None, legacy_ok=True):
    record = check.read_or_none(department_file(out, celex, key))
    if not record or record.get("version") != ACT_FIX or "bullets" not in record or record.get("celex") != celex:
        return None
    return record if basis is None or current(record, basis, legacy_ok) else None


# ------------------------------------------------------------------ run

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--steps", nargs="+", choices=STEPS, default=list(STEPS))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--count-only", action="store_true")
    args = parser.parse_args(argv)
    out = args.output_dir.resolve()
    base.OUTPUT_LANGUAGE = "de"
    screening, unsure, classification = check.new_article_runs()

    # Step 0: the change sentences, before anything reads the changed articles.
    v2_config = check.read_or_none(check.SUMMARIES_V2 / "run_config.json") or {}
    if "changes" in args.steps and not args.count_only and v2_config.get("changed_dir"):
        print("=== Step 0: the 'Was hat sich geändert' sentences (repair of the changed articles) ===", flush=True)
        argv = ["--changed-dir", v2_config["changed_dir"], "--output-dir", str(check.SUMMARIES_V2),
                "--env-file", str(args.env_file), "--workers", str(args.workers), "--redo-sentences"]
        if classification:
            argv += ["--extra-classification-dir", *map(str, classification)]
        code = changes.main(argv)
        if code == 130:
            return code
        if code:
            print("Step 0 stopped; the other steps go on with the change sentences as they are.", flush=True)

    notes = []
    legacy = legacy_bases(out)
    print("Reading the results (this takes a few minutes) ...", flush=True)
    provisions = check.screened([*check.DEFAULT_RUNS], [check.DEFAULT_UNSURE], notes)
    for run, unsure_dir in zip(screening, unsure):
        provisions.update(check.screened([run], [unsure_dir], notes))
    files = {key: found["file"] for key, found in provisions.items()}
    items = second_round(out, recheck_items(provisions)) if "recheck" in args.steps else []
    first, fixes = v1_items(check.SUMMARIES_V1, files)
    second, more = v2_items(check.SUMMARIES_V2)
    fixes.update(more)
    items += [item for item in first + second if item["step"] in args.steps]
    for note in notes:
        print(f"  {note}", flush=True)
    by_step = Counter(item["step"] for item in items)
    print("To ask Qwen: " + ", ".join(f"{step} {by_step[step]:,}" for step in QWEN_STEPS if step in args.steps)
          + f". Without Qwen: {len(fixes):,} summaries (amends note, annex start).", flush=True)
    if args.count_only:
        corrections(out, items, fixes, legacy)
        acts = affected_acts(read_json(out / "corrections.json"), check.SUMMARIES_V1, check.SUMMARIES_V2)
        flagged = {item["celex"] for item in items}
        print(f"Act and department summaries to write again: at least {len(acts):,} acts, at most "
              f"{len(set(acts) | flagged):,} (depends on the answers).", flush=True)
        return 0
    systems = {step: PROMPTS[step].read_text(encoding="utf-8").strip() + "\n" for step in QWEN_STEPS}
    systems["wording"] = WORDING_SYSTEM
    systems["act"], systems["department"] = base.system_prompt("act", "de"), base.system_prompt("department", "de")
    client = QwenClient.from_env(args.env_file, retries=1)
    workers = max(1, min(args.workers, MAX_WORKERS))
    extra = {"classified": check.classifications(classification), "provisions": provisions,
             "acts": {**(check.read_or_none(check.CLASSIFICATION / "acts.json") or {}),
                      **{celex: value for folder in classification
                         for celex, value in (check.read_or_none(folder / "acts.json") or {}).items()}}}
    records_of, tables_of = {}, {}

    def records(celex, result):
        if celex not in records_of:
            records_of[celex] = act_records(celex, result, check.SUMMARIES_V1, check.SUMMARIES_V2, extra)
        return records_of[celex]

    def ask_item(item, log):
        if item["step"] in ASK:
            return ASK[item["step"]](client, systems[item["step"]], item, log)
        rows, facts = records(item["celex"], item["corrections"])
        if item["step"] == "acts":
            if not rows:
                return {"sentences": [], "provisions": 0, "department_keys": [], "facts": dict(facts)}
            if not tables_of:
                tables_of.update(base.saved_table_groups(check.SUMMARIES_V1) or {"": {}})
            tables = {key: group for key, group in tables_of.get(item["celex"], {}).items() if "summary" in group}
            title = rows[0].get("title", "")
            subject = (extra["acts"].get(item["celex"]) or {}).get("act_subject", "")
            sentences, requests = base.summarise_act(client, systems["act"], item["celex"], title, subject,
                                                     base.act_input(rows, tables), len(rows))
            log.extend(requests)
            keys = [key for key, group in base.groups_of(item["celex"], rows).items()
                    if group["departments"] != [NO_DEPARTMENT]]
            return {"sentences": sentences, "provisions": len(rows), "department_keys": keys, "facts": dict(facts)}
        group = base.groups_of(item["celex"], rows).get(item["key"])
        if group is None:
            raise Unclassified("The provisions of this act changed since its act summary was written.")
        departments = [d for d in group["departments"] if d != NO_DEPARTMENT]
        sent = base.card_input(departments, item["celex"], rows[0].get("title", ""), item["sentences"],
                               group["provisions"])
        bullets, requests = base.summarise_department(client, systems["department"], sent)
        log.extend(requests)
        return {"departments": departments, "bullets": bullets}

    def path_of(item):
        if item["step"] == "acts":
            return act_file(out, item["celex"])
        if item["step"] == "departments":
            return department_file(out, item["celex"], item["key"])
        return answer_path(out, item)

    def worker(progress):
        def work(item):
            log, record, failed = [], {"version": VERSION if item["step"] in ASK else ACT_FIX,
                                       "step": item["step"], "celex": item["celex"],
                                       "target": item.get("target", "")}, False
            if item["step"] in ("acts", "departments"):
                record["basis"] = act_basis(item["corrections"], item["celex"])
            try:
                answer = ask_item(item, log)
                if item["step"] in ASK:
                    record["answer"] = answer
                else:
                    record.update(answer)
            except ConnectionLost:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad answer must not stop the run
                record["failure"], failed = f"{type(exc).__name__}: {exc}"[:400], True
                print(f"Could not do {item['step']} {item['celex']} {item.get('target', '')}: "
                      f"{record['failure']}", flush=True)
            record["requests"] = log
            write_json(path_of(item), record)
            progress.step(failed)
        return work

    def run_step(name, todo, saved):
        def open_items():
            left = [item for item in todo if saved(item) is None]
            return left, len(todo) - len(left)

        if todo:
            print(f"=== {name} ===", flush=True)
            run_until_done(client, name, open_items, worker, workers)

    try:
        ok, error = reachable(client)
        if not ok:
            wait_until_reachable(client, error)
        for step in QWEN_STEPS:
            todo = verify_items(out, items) if step == "verify" and "verify" in args.steps else [
                item for item in items if item["step"] == step]
            if step == "verify":
                items += todo
            run_step(step.capitalize(), todo, lambda item: saved_answer(out, item))
        corrections(out, items, fixes, legacy)
        result = read_json(out / "corrections.json")

        def legacy_ok(celex):
            return legacy.get(celex) == act_basis(result, celex)

        def act_saved(item):
            return saved_act(out, item["celex"], act_basis(result, item["celex"]), legacy_ok(item["celex"]))

        if "acts" in args.steps:
            acts = [{"step": "acts", "celex": celex, "corrections": result}
                    for celex in affected_acts(result, check.SUMMARIES_V1, check.SUMMARIES_V2)]
            print(f"Act summaries: {len(acts):,}, to write: {sum(act_saved(a) is None for a in acts):,}", flush=True)
            run_step("Acts", acts, act_saved)
        if "departments" in args.steps:
            cards = []
            for path in sorted((out / "acts").glob("*.json")) if (out / "acts").is_dir() else []:
                act = act_saved({"celex": (check.read_or_none(path) or {}).get("celex", path.stem)})
                for key in (act or {}).get("department_keys", []):
                    cards.append({"step": "departments", "celex": act["celex"], "key": key, "target": key,
                                  "sentences": act["sentences"], "corrections": result})
            todo = [card for card in cards if saved_department(
                out, card["celex"], card["key"], act_basis(result, card["celex"]), legacy_ok(card["celex"])) is None]
            print(f"Department summaries: {len(cards):,}, to write: {len(todo):,}", flush=True)
            run_step("Departments", cards, lambda item: saved_department(
                out, item["celex"], item["key"], act_basis(result, item["celex"]), legacy_ok(item["celex"])))
        if "wording" in args.steps:
            corrections(out, items, fixes, legacy)
            redone = {celex: set(entry["departments"]) for celex, entry in
                      read_json(out / "corrections.json").get("acts_redone", {}).items()}
            wording = wording_items(out, check.SUMMARIES_V1, redone)
            items += wording
            print(f"English points of department summaries: {len(wording):,}", flush=True)
            run_step("Wording", wording, lambda item: saved_answer(out, item))
    except KeyboardInterrupt:
        corrections(out, items, fixes, legacy)
        print("\nStopped by Ctrl+C. Run the same command to continue.", flush=True)
        return 130
    summary = corrections(out, items, fixes, legacy)
    print("\nFinished:")
    for key, value in summary.items():
        print(f"  {key:45} {value:,}")
    print(f"\nTo check by hand: {out / 'review.csv'}\nFor the database import: {out / 'corrections.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
