"""Explain the changes between EUR-Lex acts and route them to the departments.

Input: the export of tools/export_eurlex_changes.py (records.jsonl) and the classification results.

Steps, all resumable (Ctrl+C stops, the same command continues):
1. Changes      one Qwen request per changed article or annex (all changes of one changing act to the same
                article together), per act-level change (repeal, deferral ...) and per corrigendum.
2. Acts         one short paragraph per changing act, from the explanations of its changes.
3. Export       routing (who gets it), inbox cards per department, preview pages, review.csv, summary.json.

Who gets a change:
- the departments of the changed provision, if the classification has it (level DIRECT or INDIRECT);
- otherwise the departments of the changing act's provision that makes the change;
- otherwise nobody yet: the change is listed in to_screen.csv.
EDITORIAL changes and corrigenda that only fix wording are kept but not put in the inbox.

    python explain_eurlex_changes.py --count-only
    python explain_eurlex_changes.py --sample 40
    python explain_eurlex_changes.py
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import html
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from classify_eurlex_provisions import (  # noqa: E402
    MAX_WORKERS, ConnectionLost, Unclassified, act_links, read_json, reachable, run_until_done, safe_name, sha,
    squash, wait_until_reachable, write_json)
from eurlex_summary_checks import number_problems, wrong_language  # noqa: E402
from export_eurlex_changes import root_of  # noqa: E402
from lr_ai.qwen_client import QwenClient  # noqa: E402
from summarize_eurlex_provisions import ask  # noqa: E402

PROMPTS = {"change": ROOT / "prompts/eurlex_change_explain.md",
           "corrigendum": ROOT / "prompts/eurlex_corrigendum_explain.md",
           "act": ROOT / "prompts/eurlex_change_act_summary.md"}
TASKS = {"change": "TASK: Explain what changed. Return the JSON object described in the instructions.",
         "corrigendum": "TASK: Explain the corrigendum. Return the JSON object described in the instructions.",
         "act": "TASK: Write the paragraph. Return the JSON object described in the instructions."}
DEFAULT_EXPORT = ROOT / "runs/eurlex_changes_export_v2"
DEFAULT_CLASSIFICATION = ROOT / "runs/eurlex_classification_v5"
DEFAULT_OUTPUT = ROOT / "runs/eurlex_changes_v2"
LANGUAGES = {"de": "German", "en": "English"}
SIGNIFICANCE = ("SUBSTANTIVE", "EDITORIAL", "UNCLEAR")
AFFECTS = ("NUMBER", "DATE", "DUTY", "ADDRESSEE", "SCOPE", "WORDING_ONLY")
LEVELS = {"DIRECT": 2, "INDIRECT": 1}
REMOVALS = {"DELETE", "REPEAL", "REPEALS", "IMPLICITLY_REPEALS"}
ADDITIONS = {"ADD", "INSERT"}
# "the words ‘X’ are replaced by ‘Y’": the instruction itself names the old wording.
WORD_PAIR = re.compile(r"[‘“„\"'][^’”“\"']{1,300}[’”“\"']\s*(?:is|are|shall be) (?:replaced by|deleted)|"
                       r"[‘“„\"'][^’”“\"']{1,300}[’”“\"']\s*(?:wird|werden) durch\s*[‘“„\"']|"
                       r"[‘“„\"'][^’”“\"']{1,300}[’”“\"']\s*(?:wird|werden) gestrichen", re.I)
# Words that compare with the old wording; without the old wording they would be invented.
COMPARING = re.compile(r"\b(?:früher|bisher|bislang|vorher|zuvor|nicht mehr|nur noch|erweitert|verschärft|gelockert|"
                       r"verkürzt|verlängert|anstatt|statt|gesenkt|erhöht|previously|no longer|instead of|extended|"
                       r"tightened|relaxed|reduced|increased)\b", re.I)
PROLONGING = re.compile(r"prolong|extend|extension|postpon|defer|verlänger|verschieb", re.I)
TEXT_BUDGET = 6000            # characters per text (old, new, instruction) sent to Qwen
PASSAGE_CHARS = 450
LIMITS = {"headline": 140, "text": 800, "what": 80, "value": 300, "date": 60, "sentence": 300}
LANGUAGE = "de"               # set by main()

KIND_LABEL = {"de": {"AMENDS": "Änderung", "REPLACES": "Ersetzung", "REPEALS": "Aufhebung",
                     "IMPLICITLY_REPEALS": "Aufhebung", "CORRECTS": "Berichtigung", "DEROGATES": "Abweichung",
                     "SUSPENDS": "Aussetzung", "PARTIALLY_SUSPENDS": "Teilweise Aussetzung",
                     "EXTENDS_APPLICATION": "Verlängerung", "EXTENDS_VALIDITY": "Verlängerung",
                     "DEFERS_APPLICATION": "Verschiebung", "REESTABLISHES": "Wiederinkraftsetzung",
                     "RENDERS_OBSOLETE": "Gegenstandslos"},
              "en": {"AMENDS": "Amendment", "REPLACES": "Replacement", "REPEALS": "Repeal",
                     "IMPLICITLY_REPEALS": "Repeal", "CORRECTS": "Corrigendum", "DEROGATES": "Derogation",
                     "SUSPENDS": "Suspension", "PARTIALLY_SUSPENDS": "Partial suspension",
                     "EXTENDS_APPLICATION": "Extension", "EXTENDS_VALIDITY": "Extension",
                     "DEFERS_APPLICATION": "Deferral", "REESTABLISHES": "Reinstatement",
                     "RENDERS_OBSOLETE": "Obsolete"}}
OPERATION_LABEL = {"de": {"ADD": "neu", "INSERT": "eingefügt", "REPLACE": "ersetzt", "MODIFY": "geändert",
                          "DELETE": "gestrichen", "REPEAL": "aufgehoben", "RENUMBER": "umnummeriert",
                          "MOVE": "verschoben"},
                   "en": {"ADD": "added", "INSERT": "inserted", "REPLACE": "replaced", "MODIFY": "modified",
                          "DELETE": "deleted", "REPEAL": "repealed", "RENUMBER": "renumbered", "MOVE": "moved"}}
NOTE = {"de": "KI-generierte Erklärung. Maßgeblich ist der Originaltext.",
        "en": "AI-generated explanation. The original text decides."}


# ------------------------------------------------------------------ passages

def passages_of(text: str | None, mark: str, budget: int = TEXT_BUDGET, keep: set[str] | None = None):
    """Cut a text into numbered passages ({"O1": "..."}). Over budget, only the passages in `keep`
    (changed ones) and their neighbours are kept; the rest becomes "…"."""
    if not text:
        return "", {}
    pieces, current, changed, has_change = [], "", set(), False
    for sentence in re.split(r"(?<=[.;:])\s+(?=[A-Z(‘“0-9])", squash(text)):
        if current and len(current) + len(sentence) > PASSAGE_CHARS:
            pieces.append(current)
            if has_change:
                changed.add(len(pieces) - 1)
            current, has_change = sentence, False
        else:
            current = f"{current} {sentence}".strip()
        has_change = has_change or (keep is not None and signature(sentence) not in keep)
    if current:
        pieces.append(current)
        if has_change:
            changed.add(len(pieces) - 1)
    chosen = set(range(len(pieces)))
    if sum(map(len, pieces)) > budget and keep is not None:
        chosen = {j for i in changed for j in (i - 1, i, i + 1) if 0 <= j < len(pieces)} or set(range(3))
    passages, parts, used, skipped = {}, [], 0, False
    for index, piece in enumerate(pieces):
        if index not in chosen or used + len(piece) > budget:
            if not skipped:
                parts.append("…")
            skipped = True
            continue
        key = f"{mark}{len(passages) + 1}"
        passages[key] = piece
        parts.append(f"[{key}] {piece}")
        used += len(piece)
        skipped = False
    return " ".join(parts), passages


def signature(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", (text or "").casefold())


def other_side(text: str | None) -> set[str]:
    """Signatures of the passages of the other text, to find what changed."""
    return {signature(piece) for piece in re.split(r"(?<=[.;:])\s+(?=[A-Z(‘“0-9])", squash(text or "")) if piece}


# ------------------------------------------------------------------ items

def build_items(records: list[dict]) -> list[dict]:
    """One item per changed article (all changes of one changing act to it), per act-level change, per corrigendum."""
    grouped, items = defaultdict(list), []
    for record in records:
        if record["kind"] == "PROVISION":   # places that could not be read (root None) form one item
            root = tuple(record["root"]) if record.get("root") else None
            grouped[(record["changing"]["celex"], record["changed"]["celex"], root)].append(record)
        else:
            items.append({"id": record["id"], "kind": record["kind"], "records": [record]})
    for (changing, changed, root), rows in grouped.items():
        rows.sort(key=lambda r: r.get("ordinal") or 0)
        items.append({"id": sha([changing, changed, root])[:20], "kind": "PROVISION", "records": rows})
    return sorted(items, key=lambda item: (item["records"][0]["changed"]["celex"], item["id"]))


def item_facts(item: dict) -> dict:
    """What every step needs to know about an item, without the long texts."""
    first = item["records"][0]
    root = tuple(first["root"]) if first.get("root") else None
    days = sorted({str(r.get("effective_from"))[:10] for r in item["records"] if r.get("effective_from")})
    return {"id": item["id"], "kind": item["kind"], "relation": first["relation"],
            "changing": first["changing"], "changed": first["changed"],
            "provision": provision_name(root) if root else None, "root": list(root) if root else None,
            "provisions": [r.get("provision") for r in item["records"] if r.get("provision")],
            "operations": sorted({r.get("operation") or first["relation"] for r in item["records"]}),
            "effective_from": days[0] if days else None,
            "wording": best_wording(item["records"]),
            "instruction_locator": next((r.get("instruction_locator") for r in item["records"]
                                         if r.get("instruction_locator")), None),
            "change_ids": [r["change_id"] for r in item["records"] if r.get("change_id")],
            "relationship_id": first.get("relationship_id"),
            "changing_document_id": first.get("changing_document_id"),
            "changed_document_id": first.get("changed_document_id"),
            "missing": missing_reason(item)}


def text_situation(item: dict) -> dict:
    """Which wordings are there for an item (decides what may be said)."""
    records = item["records"]
    instruction = " ".join(r.get("instruction") or "" for r in records)
    operations = {r.get("operation") or r["relation"] for r in records} | {records[0]["relation"]}
    return {"old": any(r.get("old_text") for r in records), "new": any(r.get("new_text") for r in records),
            "instruction": bool(instruction.strip()), "pair": bool(WORD_PAIR.search(instruction)),
            "finals": bool(records[0].get("final_articles")), "operations": operations,
            "removal": bool(operations & REMOVALS), "trimmed": any(r.get("trimmed") for r in records)}


def missing_reason(item: dict) -> str | None:
    """Why there is nothing to explain (None: the item goes to Qwen). Shown as a short line built by code."""
    first, kind = item["records"][0], item["kind"]
    if kind == "CORRIGENDUM":
        if first.get("corrigendum_source") == "OTHER_LANGUAGES_ONLY":
            return "OTHER_LANGUAGES_ONLY"
        return None if first.get("corrections") or first.get("instruction") else "CORRIGENDUM_TEXT_MISSING"
    have = text_situation(item)
    if kind == "ACT":
        return None if have["instruction"] or have["finals"] else "NO_TEXT"
    if not (have["old"] or have["new"] or have["instruction"]):
        return "NO_TEXT"
    if not (have["new"] or have["instruction"] or have["removal"]):
        return "NEW_WORDING_MISSING"     # only the old wording: the new content would be invented
    return None


def provision_name(root) -> str:
    kind, number = root
    if not number:
        return "Annex"
    return f"{'Article' if kind == 'article' else 'Annex'} {number.upper() if kind == 'annex' else number}"


def best_wording(records) -> str:
    order = ["FULL", "NEW_ONLY", "OLD_ONLY", "INSTRUCTION_ONLY", "NONE"]
    values = [r.get("wording") for r in records if r.get("wording") in order]
    return min(values, key=order.index) if values else "NONE"


def request_for(item: dict) -> tuple[str, dict, dict]:
    """(prompt name, what is sent to Qwen, passages by mark)."""
    first = item["records"][0]
    acts = {"changed_act": {"celex": first["changed"]["celex"], "title": first["changed"]["title"]}}
    finals_text = " ".join(f"{f['locator']}: {f['text']}" for f in first.get("final_articles") or [])
    final_part, final_passages = passages_of(finals_text, "F", 3000)
    if item["kind"] == "CORRIGENDUM":
        sent = {"corrected_act": acts["changed_act"],
                "corrigendum": {"celex": first["changing"]["celex"], "title": first["changing"]["title"],
                                "date": first["changing"].get("date")}}
        passages = {}
        if first.get("corrections"):
            sent["corrections"] = []
            for number, correction in enumerate(first["corrections"][:12], 1):
                if correction["wrong"]:
                    passages[f"W{number}"] = correction["wrong"]
                passages[f"R{number}"] = correction["correct"]
                sent["corrections"].append({"location": correction["location"],
                                            "wrong": f"[W{number}] {correction['wrong']}" if correction["wrong"]
                                            else "(the whole part is replaced; the wrong text is not repeated)",
                                            "correct": f"[R{number}] {correction['correct']}"})
        else:
            sent["text"], passages = passages_of(first.get("instruction"), "T")
        return "corrigendum", sent, passages
    old = next((r["old_text"] for r in item["records"] if r.get("old_text")), None)
    new = next((r["new_text"] for r in item["records"] if r.get("new_text")), None)
    instruction = " ".join(dict.fromkeys(r["instruction"] for r in item["records"] if r.get("instruction")))
    old_part, old_passages = passages_of(old, "O", keep=other_side(new) if new else None)
    new_part, new_passages = passages_of(new, "N", keep=other_side(old) if old else None)
    instruction_part, instruction_passages = passages_of(instruction, "I")
    facts = item_facts(item)
    have = text_situation(item)
    changing = first["changing"]
    meta = [f"{label}: {value}" for label, value in (
        ("Date of the changing act", changing.get("date")), ("Entry into force of the changing act",
                                                             changing.get("entry_into_force")),
        ("Application of the changing act", changing.get("application")),
        ("The change applies from", facts["effective_from"])) if value]
    meta_part, meta_passages = passages_of(". ".join(meta) + "." if meta else "", "M")
    sent = {**acts, "changing_act": {"celex": first["changing"]["celex"], "title": first["changing"]["title"],
                                     "date": first["changing"].get("date")},
            "provision": facts["provision"] or ("several places, see the instruction" if item["kind"] == "PROVISION"
                                                else "the whole act"),
            "operations": facts["operations"], "effective_from": facts["effective_from"]}
    for key, value in (("old_text", old_part), ("new_text", new_part), ("instruction", instruction_part),
                       ("final_articles", final_part), ("dates_known", meta_part)):
        if value:
            sent[key] = value
    notes = []
    if not old_part and not have["pair"] and not have["removal"]:
        notes.append("The old wording is not given: describe only what applies now, compare with nothing.")
    if have["trimmed"]:
        notes.append("The provision is long: only the changed parts and their neighbours are given.")
    if notes:
        sent["notes"] = notes
    return "change", sent, {**old_passages, **new_passages, **instruction_passages, **final_passages,
                            **meta_passages}


# ------------------------------------------------------------------ schemas and checks

def facts_schema(passage_ids, before=True):
    """No passages, no facts: an empty list of marks would force Qwen to invent one."""
    return {"type": "array", "maxItems": 5 if passage_ids else 0, "items": fact_schema(passage_ids, before)}


def fact_schema(passage_ids, before=True):
    """before=False: no old wording given, so before must stay empty (enforced by the schema)."""
    return {"type": "object", "additionalProperties": False, "required": ["what", "before", "after", "passages"],
            "properties": {"what": {"type": "string", "maxLength": LIMITS["what"]},
                           "before": {"type": "string", "maxLength": LIMITS["value"] if before else 0},
                           "after": {"type": "string", "maxLength": LIMITS["value"]},
                           "passages": {"type": "array", "minItems": 1, "maxItems": 4,
                                        "items": {"type": "string", "enum": list(passage_ids) or ["NONE"]}}}}


def change_schema(passage_ids, before=True):
    return {"type": "object", "additionalProperties": False,
            "required": ["headline", "what_changed", "facts", "dates", "significance"],
            "properties": {
                "headline": {"type": "string", "maxLength": LIMITS["headline"]},
                "what_changed": {"type": "string", "maxLength": LIMITS["text"]},
                "facts": facts_schema(passage_ids, before),
                "dates": {"type": "array", "maxItems": 4, "items": {
                    "type": "object", "additionalProperties": False, "required": ["what", "date", "passages"],
                    "properties": {"what": {"type": "string", "maxLength": LIMITS["what"]},
                                   "date": {"type": "string", "maxLength": LIMITS["date"]},
                                   "passages": {"type": "array", "maxItems": 3,
                                                "items": {"type": "string", "enum": list(passage_ids) or ["NONE"]}}}}},
                "significance": {"type": "string", "enum": list(SIGNIFICANCE)}}}


def corrigendum_schema(passage_ids):
    return {"type": "object", "additionalProperties": False,
            "required": ["headline", "what_was_wrong", "facts", "affects"],
            "properties": {"headline": {"type": "string", "maxLength": LIMITS["headline"]},
                           "what_was_wrong": {"type": "string", "maxLength": LIMITS["text"]},
                           "facts": facts_schema(passage_ids),
                           "affects": {"type": "string", "enum": list(AFFECTS)}}}


ACT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["sentences"],
              "properties": {"sentences": {"type": "array", "minItems": 2, "maxItems": 4,
                                           "items": {"type": "string", "maxLength": LIMITS["sentence"]}}}}


def text_field(reply, key, limit):
    value = squash(reply.get(key)) if isinstance(reply.get(key), str) else ""
    if not value or len(value) > limit:
        raise Unclassified(f"{key} must be a text of 1 to {limit} characters.")
    return value


def check(texts, source, what="text"):
    problems = number_problems([t for t in texts if t], source)
    if problems:
        raise Unclassified(f"In {what}: {problems[0]}")


def check_language(texts):
    if wrong_language([t for t in texts if t], LANGUAGE):
        raise Unclassified(f"Write everything in {LANGUAGES[LANGUAGE]}.")


def side_text(passages: dict, prefix: str) -> str:
    return " ".join(text for key, text in passages.items() if key.startswith(prefix))


def clean_facts(reply, passages, old_marks, new_marks):
    facts = []
    for fact in reply.get("facts") or []:
        if not isinstance(fact, dict) or set(fact) != {"what", "before", "after", "passages"}:
            raise Unclassified("Every fact holds exactly what, before, after and passages.")
        cited = [mark for mark in fact["passages"] if mark in passages]
        if not cited or len(cited) != len(fact["passages"]):
            raise Unclassified("passages must name passage marks from the input, for example O2 or N2.")
        before, after = squash(fact["before"]), squash(fact["after"])
        if not squash(fact["what"]) or not (before or after):
            raise Unclassified("A fact needs what, and before or after.")
        old_text = " ".join(passages[m] for m in passages if m[0] in old_marks)
        new_text = " ".join(passages[m] for m in passages if m[0] in new_marks)
        if before and not old_text:
            raise Unclassified("before must stay empty: the old wording is not given.")
        if before:
            check([before], old_text, f"before of '{fact['what']}'")
        if after:
            check([after], new_text, f"after of '{fact['what']}'")
        facts.append({"what": squash(fact["what"]), "before": before, "after": after, "passages": cited})
    return facts


def validate_change(reply, passages, sent, have=None):
    if not isinstance(reply, dict) or set(reply) != {"headline", "what_changed", "facts", "dates", "significance"}:
        raise Unclassified("The reply must hold exactly headline, what_changed, facts, dates and significance.")
    headline = text_field(reply, "headline", LIMITS["headline"])
    text = text_field(reply, "what_changed", LIMITS["text"])
    if reply["significance"] not in SIGNIFICANCE:
        raise Unclassified(f"significance is one of {', '.join(SIGNIFICANCE)}.")
    every = json.dumps(sent, ensure_ascii=False)
    check([headline, text], every, "headline or what_changed")
    have = have or {}
    old_marks = {"O"} | ({"I"} if have.get("pair") else set())
    facts = clean_facts(reply, passages, old_marks, {"N", "I", "F", "M"})
    if compares_without_old(have, sent) and (word := COMPARING.search(f"{headline} {text}")):
        raise Unclassified(f"'{word.group(0)}' compares with the old wording, which is not given. "
                           "Describe only what applies now.")
    if reply["significance"] == "EDITORIAL" and any(
            number_problems([fact["after"]], fact["before"]) for fact in facts if fact["before"] and fact["after"]):
        raise Unclassified("A fact changes a number or date, so the change is not EDITORIAL. Use SUBSTANTIVE.")
    dates = []
    for item in reply.get("dates") or []:
        if not isinstance(item, dict) or set(item) != {"what", "date", "passages"}:
            raise Unclassified("Every date holds exactly what, date and passages.")
        value = squash(item["date"])
        if not value or not re.search(r"\d", value):
            raise Unclassified("date must be a date from the text, for example 31. Dezember 2027.")
        check([value], every, "dates")
        dates.append({"what": squash(item["what"]), "date": value,
                      "passages": [mark for mark in item["passages"] if mark in passages]})
    check_language([headline, text, *(f["what"] for f in facts)])
    return {"headline": headline, "what_changed": text, "facts": facts, "dates": dates,
            "significance": reply["significance"]}


def compares_without_old(have: dict, sent: dict) -> bool:
    """True when a comparison with the old wording cannot be backed by the texts given."""
    if not have or have.get("old") or have.get("pair") or have.get("removal"):
        return False
    operations = set(have.get("operations", set())) - {"AMENDS", "REPLACES", "OTHER"}
    if operations and operations <= ADDITIONS:
        return False                       # a new provision only: "neu", "zusätzlich" are fine
    title = (sent.get("changing_act") or {}).get("title", "")
    return not (PROLONGING.search(title) or have.get("operations", set()) & {
        "EXTENDS_APPLICATION", "EXTENDS_VALIDITY", "DEFERS_APPLICATION"})


def validate_corrigendum(reply, passages, sent):
    if not isinstance(reply, dict) or set(reply) != {"headline", "what_was_wrong", "facts", "affects"}:
        raise Unclassified("The reply must hold exactly headline, what_was_wrong, facts and affects.")
    headline = text_field(reply, "headline", LIMITS["headline"])
    text = text_field(reply, "what_was_wrong", LIMITS["text"])
    if reply["affects"] not in AFFECTS:
        raise Unclassified(f"affects is one of {', '.join(AFFECTS)}.")
    check([headline, text], json.dumps(sent, ensure_ascii=False), "headline or what_was_wrong")
    facts = clean_facts(reply, passages, {"W", "T"}, {"R", "T"})
    if not facts and any(mark.startswith("R") for mark in passages):
        raise Unclassified("Name at least one correction in facts, with the correct text in after.")
    if reply["affects"] == "WORDING_ONLY" and any(
            number_problems([fact["after"]], fact["before"]) for fact in facts if fact["before"] and fact["after"]):
        raise Unclassified("A correction changes a number or date, so affects is not WORDING_ONLY. Use NUMBER or DATE.")
    check_language([headline, text])
    return {"headline": headline, "what_was_wrong": text, "facts": facts, "affects": reply["affects"]}


def validate_act(reply, source):
    sentences = reply.get("sentences") if isinstance(reply, dict) and set(reply) == {"sentences"} else None
    if not isinstance(sentences, list) or not 2 <= len(sentences) <= 4:
        raise Unclassified("The reply holds exactly sentences, a list of two to four full sentences.")
    cleaned = [squash(s) for s in sentences if isinstance(s, str) and squash(s)]
    if len(cleaned) != len(sentences) or any(len(s) > LIMITS["sentence"] for s in cleaned):
        raise Unclassified(f"Every sentence is a text of 1 to {LIMITS['sentence']} characters.")
    check(cleaned, source, "sentences")
    check_language(cleaned)
    return cleaned


def explain(client, systems, item, log):
    name, sent, passages = request_for(item)
    have = text_situation(item)
    if name == "corrigendum":
        schema, check_reply = corrigendum_schema(passages), lambda reply: validate_corrigendum(reply, passages, sent)
    else:
        before = any(mark.startswith("O") for mark in passages) or have["pair"]
        schema = change_schema(passages, before)
        check_reply = lambda reply: validate_change(reply, passages, sent, have)
    try:
        answer = ask(client, systems[name], sent, TASKS[name], schema, f"{name}_explanation", check_reply, log, 1200)
    except OverflowError:
        raise Unclassified("The texts of this change are too long for the model.") from None
    for fact in answer["facts"]:
        fact["quotes"] = [passages[mark][:400] for mark in fact["passages"]]
    return name, answer


# ------------------------------------------------------------------ routing

def locator_index(results):
    """celex -> root -> [(locator, result)] of the classified provisions."""
    index = defaultdict(lambda: defaultdict(list))
    for celex, rows in results.items():
        for locator, result in rows.items():
            root = root_of(locator)
            if root:
                index[celex][root].append((locator, result))
    return index


def best(found):
    found = [(locator, r) for locator, r in found if r.get("level") in LEVELS]
    if not found:
        return None
    level = max(found, key=lambda pair: LEVELS[pair[1]["level"]])[1]["level"]
    departments = sorted({d for _, r in found for d in (r.get("departments") or []) + (r.get("secondary_departments") or [])})
    top = max(found, key=lambda pair: LEVELS[pair[1]["level"]])[1]
    return {"level": level, "departments": departments, "domain": top.get("primary_domain"),
            "domain_title": top.get("primary_title"), "matched": [locator for locator, _ in found][:5]}


def route(facts, results, index):
    """Who gets a change, and why."""
    changed, changing = facts["changed"]["celex"], facts["changing"]["celex"]
    if facts["root"]:
        found = best(index[changed].get(tuple(facts["root"]), []))
        if found:
            return {**found, "route": "CHANGED_PROVISION"}
    if facts["kind"] in {"ACT", "CORRIGENDUM"} and changed in results:
        found = best(list(results[changed].items()))
        if found:
            return {**found, "route": "CHANGED_ACT"}
    if facts.get("instruction_locator"):
        found = best([(facts["instruction_locator"], r) for loc, r in results.get(changing, {}).items()
                      if root_of(loc) == root_of(facts["instruction_locator"])])
        if found:
            return {**found, "route": "CHANGING_PROVISION"}
    if changing in results:
        found = best(list(results[changing].items()))
        if found:
            return {**found, "route": "CHANGING_ACT"}
    return {"level": None, "departments": [], "domain": None, "domain_title": None, "matched": [],
            "route": "NOT_SCREENED"}


def goes_to_inbox(record) -> bool:
    explanation, routing = record.get("explanation") or {}, record.get("routing") or {}
    if routing.get("level") not in LEVELS:
        return False
    if record["kind"] == "CORRIGENDUM":
        return bool(explanation) and explanation.get("affects") != "WORDING_ONLY"
    if record["kind"] == "ACT" and record["relation"] in {"REPEALS", "IMPLICITLY_REPEALS"}:
        return True
    return explanation.get("significance") != "EDITORIAL"


# ------------------------------------------------------------------ export

MISSING_NOTE = {"de": {"NEW_WORDING_MISSING": "Der neue Wortlaut liegt nicht vor.",
                       "NO_TEXT": "Der Wortlaut der Änderung liegt nicht vor.",
                       "CORRIGENDUM_TEXT_MISSING": "Der Text der Berichtigung liegt nicht vor.",
                       "OTHER_LANGUAGES_ONLY": "Betrifft nur andere Sprachfassungen (nicht Deutsch oder Englisch)."},
                "en": {"NEW_WORDING_MISSING": "The new wording is not available.",
                       "NO_TEXT": "The wording of the change is not available.",
                       "CORRIGENDUM_TEXT_MISSING": "The text of the corrigendum is not available.",
                       "OTHER_LANGUAGES_ONLY": "Concerns other language versions only (not German or English)."}}


def code_line(facts, language):
    """A short line built by code when there is no text to explain: what, where, by which act, from when."""
    label = KIND_LABEL[language].get(facts["relation"], facts["relation"])
    day = facts["effective_from"] or facts["changing"].get("application") or facts["changing"].get("entry_into_force")
    operations = ", ".join(OPERATION_LABEL[language].get(o, "") for o in facts["operations"]
                           if OPERATION_LABEL[language].get(o))
    place = f"{facts['provision']} " if facts.get("provision") else ""
    note = MISSING_NOTE[language].get(facts.get("missing") or "", "")
    if language == "de":
        line = f"{label}: {place}{facts['changed']['celex']}" + (f" {operations}" if operations else "") + \
            f" durch {facts['changing']['celex']}" + (f", ab {day}" if day else "")
    else:
        line = f"{label}: {place}{facts['changed']['celex']}" + (f" {operations}" if operations else "") + \
            f" by {facts['changing']['celex']}" + (f", from {day}" if day else "")
    return f"{line}. {note}".strip()


def entry(record, language):
    facts, explanation = record["facts"], record.get("explanation") or {}
    first = record["texts"]
    return {"id": facts["id"], "kind": record["kind"], "relation": facts["relation"],
            "label": KIND_LABEL[language].get(facts["relation"], facts["relation"]),
            "operations": [OPERATION_LABEL[language].get(o, o) for o in facts["operations"]],
            "provision": facts["provision"], "provisions": facts["provisions"],
            "changing": {**facts["changing"], **act_links(facts["changing"]["celex"])},
            "effective_from": facts["effective_from"], "wording": facts["wording"],
            "headline": explanation.get("headline") or code_line(facts, language),
            "text": explanation.get("what_changed") or explanation.get("what_was_wrong") or "",
            "facts": explanation.get("facts", []), "dates": explanation.get("dates", []),
            "significance": explanation.get("significance") or explanation.get("affects"),
            "level": record["routing"].get("level"), "route": record["routing"].get("route"),
            "old_text": (first.get("old") or "")[:4000], "new_text": (first.get("new") or "")[:4000]}


STYLE = """body{font-family:Segoe UI,Arial,sans-serif;margin:24px auto;max-width:980px;color:#1c2430;background:#f5f6f8}
.card{background:#fff;border:1px solid #d9dde3;border-radius:8px;padding:16px 20px;margin:16px 0}
.card h2{font-size:17px;margin:0 0 4px}.meta{color:#5b6675;font-size:13px}.change{border-top:1px solid #e3e6eb;padding:10px 0}
.tag{display:inline-block;font-size:12px;border-radius:10px;padding:1px 9px;margin-right:6px;font-weight:600;background:#e6ecf2}
.tag.Berichtigung,.tag.Corrigendum{background:#fff1d6}.tag.Aufhebung,.tag.Repeal{background:#fde2e2}
.fact{font-size:14px;margin:2px 0}.before{color:#8a3b00;text-decoration:line-through}.after{color:#0b6b2f;font-weight:600}
details{margin-top:6px}pre{white-space:pre-wrap;font-size:12px;background:#f7f7f9;padding:8px}.note{color:#7a8594;font-size:12px}"""


def card_html(card, language):
    e = html.escape
    out = [f'<div class="card"><h2>{e(card["title"] or card["celex"])}</h2>'
           f'<div class="meta">{e(card["celex"])} · <a href="{e(card["eurlex_url"])}">EUR-Lex</a></div>']
    for change in card["changes"]:
        out.append(f'<div class="change"><span class="tag {e(change["label"])}">{e(change["label"])}</span>'
                   f'<b>{e(change["provision"] or "")} {e(change["headline"])}</b>'
                   f'<div class="meta">{e(change["changing"]["celex"])} {e(change["changing"]["title"][:120])}'
                   f'{" · ab " + e(change["effective_from"]) if change["effective_from"] else ""}</div>'
                   f'<p>{e(change["text"])}</p>')
        for fact in change["facts"]:
            out.append(f'<div class="fact">{e(fact["what"])}: <span class="before">{e(fact["before"])}</span> → '
                       f'<span class="after">{e(fact["after"])}</span></div>')
        for day in change["dates"]:
            out.append(f'<div class="fact">{e(day["what"])}: <b>{e(day["date"])}</b></div>')
        if change["old_text"] or change["new_text"]:
            out.append(f'<details><summary>{"Wortlaut vorher / nachher" if language == "de" else "Old / new wording"}'
                       f'</summary><pre>{e(change["old_text"])}</pre><pre>{e(change["new_text"])}</pre></details>')
        out.append("</div>")
    out.append(f'<div class="note">{e(NOTE[language])}</div></div>')
    return "".join(out)


CHANGED_BY = {
    "de": "Dieser Rechtsakt wurde {count}-mal geändert oder berichtigt, durch {acts} Rechtsakt(e). {latest}",
    "en": "This act was changed or corrected {count} time(s), by {acts} act(s). {latest}"}
LATEST = {"de": "Zuletzt: {label} durch {celex}{day}: {headline}.", "en": "Latest: {label} by {celex}{day}: {headline}."}
SINCE = {"de": " (gilt ab {day})", "en": " (applies from {day})"}


def link_for(template, facts):
    """The link to the change in the frontend, from --link-template. Empty when no template is given."""
    if not template:
        return ""
    values = {"item_id": facts["id"], "change_id": (facts.get("change_ids") or [""])[0],
              "relationship_id": facts.get("relationship_id") or "",
              "changed_celex": facts["changed"]["celex"], "changing_celex": facts["changing"]["celex"],
              "changed_document_id": facts.get("changed_document_id") or "",
              "changing_document_id": facts.get("changing_document_id") or ""}
    return template.format(**values)


def page_item(record, language, template):
    facts, explanation = record["facts"], record.get("explanation") or {}
    text = explanation.get("what_changed") or explanation.get("what_was_wrong") or ""
    return {"id": facts["id"], "kind": record["kind"], "relation": facts["relation"],
            "label": KIND_LABEL[language].get(facts["relation"], facts["relation"]),
            "provision": facts["provision"], "changed": facts["changed"], "changing": facts["changing"],
            "headline": explanation.get("headline") or code_line(facts, language),
            "short": re.split(r"(?<=[.!?])\s+", text)[0] if text else "",
            "effective_from": facts["effective_from"],
            "significance": explanation.get("significance") or explanation.get("affects"),
            "change_ids": facts.get("change_ids", []), "relationship_id": facts.get("relationship_id"),
            "changing_document_id": facts.get("changing_document_id"),
            "changed_document_id": facts.get("changed_document_id"), "link": link_for(template, facts)}


def act_pages(out, done, acts, language, template=""):
    """One file per act for the act page: the changes this act makes, and the changes made to it."""
    made, received, titles = defaultdict(list), defaultdict(list), {}
    for record in done:
        item = page_item(record, language, template)
        made[item["changing"]["celex"]].append(item)
        received[item["changed"]["celex"]].append(item)
        titles[item["changing"]["celex"]] = item["changing"]["title"]
        titles[item["changed"]["celex"]] = item["changed"]["title"]
    order = lambda item: (item["effective_from"] or "", item["provision"] or "")  # noqa: E731
    for celex in sorted(set(made) | set(received)):
        page = {"celex": celex, "title": titles.get(celex, ""), "changes_made": None, "changed_by": None,
                "note": NOTE[language]}
        if made.get(celex):
            items = sorted(made[celex], key=order, reverse=True)
            page["changes_made"] = {
                "summary": acts.get(celex) or [],
                "count": len(items),
                "changed_acts": [{"celex": c, "title": next(i["changed"]["title"] for i in items if i["changed"]["celex"] == c),
                                  "count": n} for c, n in Counter(i["changed"]["celex"] for i in items).most_common()],
                "items": items}
        if received.get(celex):
            items = sorted(received[celex], key=order, reverse=True)
            latest = next((i for i in items if i["significance"] not in ("EDITORIAL", "WORDING_ONLY")), items[0])
            day = SINCE[language].format(day=latest["effective_from"]) if latest["effective_from"] else ""
            line = CHANGED_BY[language].format(
                count=len(items), acts=len({i["changing"]["celex"] for i in items}),
                latest=LATEST[language].format(label=latest["label"], celex=latest["changing"]["celex"], day=day,
                                               headline=latest["headline"].rstrip(".")))
            page["changed_by"] = {"summary_line": line, "count": len(items),
                                  "changing_acts": [{"celex": c, "title": next(i["changing"]["title"] for i in items
                                                                               if i["changing"]["celex"] == c),
                                                     "count": n, "summary": acts.get(c) or []}
                                                    for c, n in Counter(i["changing"]["celex"] for i in items).most_common()],
                                  "items": items}
        write_json(out / "act_pages" / f"{safe_name(celex)}.json", page)
        (out / "preview" / "acts").mkdir(parents=True, exist_ok=True)
        (out / "preview" / "acts" / f"{safe_name(celex)}.html").write_text(page_html(page, language), encoding="utf-8")
    names = sorted(set(made) | set(received))
    (out / "preview" / "acts" / "index.html").write_text(
        f'<!doctype html><html><head><meta charset="utf-8"><title>Act pages</title><style>{STYLE}</style></head><body>'
        "<h1>Act pages</h1><ul>" + "".join(f'<li><a href="{safe_name(c)}.html">{html.escape(c)}</a> '
                                          f'{html.escape(titles.get(c, "")[:120])}</li>' for c in names)
        + "</ul></body></html>", encoding="utf-8")
    return len(names)


PAGE_WORDS = {"de": {"made": "Änderungen durch diesen Rechtsakt", "by": "Geändert durch", "open": "Zur Änderung"},
              "en": {"made": "Changes made by this act", "by": "Changed by", "open": "Open change"}}


def page_html(page, language):
    e, words = html.escape, PAGE_WORDS[language]

    def rows(items, other):
        return "".join(
            f'<div class="change"><span class="tag {e(i["label"])}">{e(i["label"])}</span>'
            f'<b>{e(i[other]["celex"])} {e(i["provision"] or "")}</b> — {e(i["headline"])}'
            f'{" · " + e(i["effective_from"]) if i["effective_from"] else ""}<div>{e(i["short"])}</div>'
            + (f'<a href="{e(i["link"])}">{e(words["open"])}</a>' if i["link"] else "") + "</div>" for i in items)

    parts = [f'<!doctype html><html lang="{language}"><head><meta charset="utf-8"><title>{e(page["celex"])}</title>'
             f'<style>{STYLE}</style></head><body><p><a href="index.html">&larr;</a></p>'
             f'<h1>{e(page["celex"])}</h1><p>{e(page["title"])}</p>']
    if page["changes_made"]:
        made = page["changes_made"]
        parts.append(f'<div class="card"><h2>{e(words["made"])} ({made["count"]})</h2><p>{e(" ".join(made["summary"]))}</p>'
                     + rows(made["items"], "changed") + "</div>")
    if page["changed_by"]:
        by = page["changed_by"]
        parts.append(f'<div class="card"><h2>{e(words["by"])} ({by["count"]})</h2><p>{e(by["summary_line"])}</p>'
                     + rows(by["items"], "changing") + "</div>")
    parts.append(f'<div class="note">{e(page["note"])}</div></body></html>')
    return "".join(parts)


def export(out, planned, language, template=""):
    records = []
    for item in planned:
        path = out / "items" / f"{item['id']}.json"
        if path.is_file():
            records.append(read_json(path))
    done = [r for r in records if "explanation" in r or r.get("no_text")]
    failed = [r for r in records if "failure" in r]
    acts = {}
    for path in (out / "acts").glob("*.json") if (out / "acts").is_dir() else []:
        saved = read_json(path)
        acts[saved["celex"]] = saved.get("sentences", [])
    inbox = defaultdict(lambda: defaultdict(list))
    rows, to_screen = [], []
    titles = {r["facts"]["changed"]["celex"]: r["facts"]["changed"]["title"] for r in done}
    for record in done:
        item = entry(record, language)
        if record["routing"]["route"] == "NOT_SCREENED":
            to_screen.append(record)
        if goes_to_inbox(record):
            for department in record["routing"]["departments"] or ["(no department yet)"]:
                inbox[department][record["facts"]["changed"]["celex"]].append(item)
        rows.append([record["facts"]["changed"]["celex"], item["provision"] or "", record["facts"]["changing"]["celex"],
                     item["label"], ", ".join(item["operations"]), item["effective_from"] or "", item["wording"],
                     item["significance"] or "", item["level"] or "", record["routing"]["route"],
                     ", ".join(record["routing"]["departments"]), item["headline"], item["text"],
                     " | ".join(f"{f['what']}: {f['before']} -> {f['after']}" for f in item["facts"]),
                     " | ".join(f"{d['what']}: {d['date']}" for d in item["dates"]),
                     (item["old_text"] or "")[:300], (item["new_text"] or "")[:300]])
    cards_total = 0
    (out / "preview").mkdir(parents=True, exist_ok=True)
    index = []
    for department, by_act in sorted(inbox.items()):
        cards = []
        for celex, changes in sorted(by_act.items()):
            changes.sort(key=lambda c: (c["effective_from"] or "", c["provision"] or ""), reverse=True)
            cards.append({"celex": celex, "title": titles.get(celex, ""), **act_links(celex),
                          "changes": changes, "changing_acts": {c["changing"]["celex"]: acts.get(c["changing"]["celex"], [])
                                                                for c in changes},
                          "note": NOTE[language]})
        cards.sort(key=lambda c: max((ch["effective_from"] or "") for ch in c["changes"]), reverse=True)
        cards_total += len(cards)
        name = safe_name(department)
        write_json(out / "inbox" / f"{name}.json", {"department": department, "acts": len(cards), "cards": cards})
        (out / "preview" / f"{name}.html").write_text(
            f'<!doctype html><html lang="{language}"><head><meta charset="utf-8"><title>{html.escape(department)}</title>'
            f'<style>{STYLE}</style></head><body><p><a href="index.html">&larr;</a></p><h1>{html.escape(department)}</h1>'
            + "".join(card_html(card, language) for card in cards) + "</body></html>", encoding="utf-8")
        index.append((department, name, len(cards)))
    (out / "preview" / "index.html").write_text(
        f'<!doctype html><html><head><meta charset="utf-8"><title>Legal Radar – Änderungen</title><style>{STYLE}</style>'
        f'</head><body><h1>Legal Radar – Änderungen</h1><ul>'
        + "".join(f'<li><a href="{n}.html">{html.escape(d)}</a> — {c}</li>' for d, n, c in index)
        + "</ul></body></html>", encoding="utf-8")
    with (out / "review.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["changed_act", "provision", "changing_act", "kind", "operations", "effective_from", "wording",
                         "significance", "level", "route", "departments", "headline", "explanation", "facts",
                         "dates", "old_start", "new_start", "explanation correct (yes/no)", "missing or wrong",
                         "significance right (yes/no)", "comment"])
        writer.writerows(rows)
    with (out / "missing_text.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["changed_act", "provision", "changing_act", "relation", "kind", "wording", "reason"])
        for record in done:
            if record.get("no_text"):
                writer.writerow([record["facts"]["changed"]["celex"], record["facts"]["provision"] or "",
                                 record["facts"]["changing"]["celex"], record["facts"]["relation"], record["kind"],
                                 record["facts"]["wording"], record["facts"].get("missing") or ""])
    with (out / "to_screen.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["changed_act", "provision", "changing_act", "relation", "significance", "headline"])
        for record in to_screen:
            ex = record.get("explanation") or {}
            writer.writerow([record["facts"]["changed"]["celex"], record["facts"]["provision"] or "",
                             record["facts"]["changing"]["celex"], record["facts"]["relation"],
                             ex.get("significance") or ex.get("affects") or "", ex.get("headline", "")])
    summary = {"language": language, "items_planned": len(planned), "explained": len(done), "failed": len(failed),
               "open": len(planned) - len(done) - len(failed),
               "by_kind": dict(Counter(r["kind"] for r in done)),
               "significance": dict(Counter((r.get("explanation") or {}).get("significance")
                                            or (r.get("explanation") or {}).get("affects") or "NO_TEXT" for r in done)),
               "wording": dict(Counter(r["facts"]["wording"] for r in done)),
               "route": dict(Counter(r["routing"]["route"] for r in done)),
               "missing_text": dict(Counter(r["facts"].get("missing") or "NO_TEXT" for r in done if r.get("no_text"))),
               "in_inbox": sum(goes_to_inbox(r) for r in done), "departments": len(inbox), "cards": cards_total,
               "changing_acts_with_summary": sum(1 for v in acts.values() if v),
               "failures": [{"id": r["facts"]["id"], "changed": r["facts"]["changed"]["celex"],
                             "provision": r["facts"]["provision"], "changing": r["facts"]["changing"]["celex"],
                             "error": r["failure"]} for r in failed]}
    summary["act_pages"] = act_pages(out, done, acts, language, template)
    write_json(out / "summary.json", summary)
    return summary


# ------------------------------------------------------------------ the run

def pick_sample(items, count):
    """A fixed mix: corrigenda, act-level changes, and provision changes of each wording kind."""
    ordered = sorted(items, key=lambda item: sha([item["id"]]))
    groups = defaultdict(list)
    for item in ordered:
        key = item["kind"] if item["kind"] != "PROVISION" else best_wording(item["records"])
        groups[key].append(item)
    chosen, queues = [], [groups[key] for key in sorted(groups)]
    while len(chosen) < count and any(queues):
        for queue in queues:
            if queue and len(chosen) < count:
                chosen.append(queue.pop(0))
    return chosen


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--export-dir", type=Path, default=DEFAULT_EXPORT)
    parser.add_argument("--classification-dir", type=Path, default=DEFAULT_CLASSIFICATION)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--language", choices=tuple(LANGUAGES), default="de")
    parser.add_argument("--sample", type=int, help="Explain only this many items (a fixed mix of kinds).")
    parser.add_argument("--celex", nargs="+", default=[], help="Only changes of these acts (changed or changing).")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--link-template", default="",
                        help="Link to a change in the frontend, for example "
                             "'/acts/{changed_document_id}/changes?change={change_id}'. Placeholders: item_id, change_id, "
                             "relationship_id, changed_celex, changing_celex, changed_document_id, changing_document_id.")
    parser.add_argument("--count-only", action="store_true")
    args = parser.parse_args(argv)
    global LANGUAGE
    LANGUAGE = args.language
    out = args.output_dir.resolve()
    try:
        records = [json.loads(line) for line in (args.export_dir / "records.jsonl").read_text(encoding="utf-8").splitlines()
                   if line.strip()]
        results = read_json(args.classification_dir / "results.json")
        systems = {name: path.read_text(encoding="utf-8").replace("<LANGUAGE>", LANGUAGES[args.language]).strip() + "\n"
                   for name, path in PROMPTS.items()}
    except (OSError, ValueError) as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 1
    items = build_items(records)
    if args.celex:
        wanted = {c.strip().upper() for c in args.celex}
        items = [i for i in items if {i["records"][0]["changed"]["celex"], i["records"][0]["changing"]["celex"]} & wanted]
    index = locator_index(results)
    print(f"{len(records):,} change records, {len(items):,} items to explain: "
          f"{dict(Counter(i['kind'] for i in items))}", flush=True)
    routes = Counter(route(item_facts(i), results, index)["route"] for i in items)
    print(f"Routing: {dict(routes)}", flush=True)
    if args.count_only:
        return 0
    if not items:
        print("Nothing to explain.", file=sys.stderr)
        return 1
    fingerprint = sha([systems, TASKS, change_schema(["O1"]), change_schema(["N1"], False), corrigendum_schema(["W1"]),
                       ACT_SCHEMA, args.language, "v1.2"])
    config = out / "run_config.json"
    if config.is_file() and read_json(config).get("fingerprint") != fingerprint:
        print("The prompts or the language changed since this folder was started. Use a new --output-dir.",
              file=sys.stderr)
        return 1
    write_json(config, {"fingerprint": fingerprint, "language": args.language, "export": str(args.export_dir),
                        "system_prompts": systems})
    planned = pick_sample(items, args.sample) if args.sample else items
    print(f"Planned: {len(planned):,} items.", flush=True)

    client = QwenClient.from_env(args.env_file, retries=1)
    workers = max(1, min(args.workers, MAX_WORKERS))

    def saved(item):
        path = out / "items" / f"{item['id']}.json"
        record = read_json(path) if path.is_file() else {}
        return record if ("explanation" in record or record.get("no_text")) else None

    def open_items():
        todo = [item for item in planned if saved(item) is None]
        return todo, len(planned) - len(todo)

    def item_worker(progress):
        def work(item):
            facts = item_facts(item)
            record = {"kind": item["kind"], "relation": facts["relation"], "facts": facts,
                      "routing": route(facts, results, index),
                      "texts": {"old": next((r["old_text"] for r in item["records"] if r.get("old_text")), None),
                                "new": next((r["new_text"] for r in item["records"] if r.get("new_text")), None)}}
            failed = False
            if facts["missing"]:
                record["no_text"] = True       # nothing to explain: a line built by code, listed in missing_text.csv
            else:
                log = []
                try:
                    record["prompt"], record["explanation"] = explain(client, systems, item, log)
                except ConnectionLost:
                    raise
                except Unclassified as exc:
                    record["failure"], failed = str(exc), True
                except Exception as exc:  # noqa: BLE001 - one bad record must not stop the run
                    record["failure"], failed = f"{type(exc).__name__}: {exc}"[:400], True
                record["requests"] = log
            if failed:
                print(f"Could not explain {facts['changed']['celex']} {facts['provision'] or facts['relation']} "
                      f"({facts['changing']['celex']}): {record['failure']}", flush=True)
            write_json(out / "items" / f"{item['id']}.json", record)
            progress.step(failed)
        return work

    def changing_acts():
        acts = defaultdict(list)
        for item in planned:
            if (record := saved(item)) and record.get("explanation"):
                acts[record["facts"]["changing"]["celex"]].append(record)
        return acts

    def open_acts():
        acts = changing_acts()
        todo = []
        for celex, records_ in sorted(acts.items()):
            path = out / "acts" / f"{safe_name(celex)}.json"
            current = read_json(path) if path.is_file() else {}
            if not ("sentences" in current and current.get("items") == len(records_)):
                todo.append((celex, records_))
        return todo, len(acts) - len(todo)

    def act_worker(progress):
        def work(job):
            celex, records_ = job
            first = records_[0]["facts"]["changing"]
            changes = sorted(records_, key=lambda r: (r["explanation"].get("significance") != "SUBSTANTIVE"))[:12]
            sent = {"changing_act": {"celex": celex, "title": first["title"], "date": first.get("date")},
                    "changed_acts": [{"celex": c, "provisions": n} for c, n in
                                     Counter(r["facts"]["changed"]["celex"] for r in records_).most_common(5)],
                    "changes": [{"provision": r["facts"]["provision"] or r["facts"]["relation"],
                                 "headline": r["explanation"]["headline"],
                                 "what_changed": r["explanation"].get("what_changed") or r["explanation"].get("what_was_wrong"),
                                 "significance": r["explanation"].get("significance") or r["explanation"].get("affects")}
                                for r in changes],
                    "dates": sorted({f"{d['what']}: {d['date']}" for r in records_ for d in r["explanation"].get("dates", [])})[:6]}
            record, log, failed = {"celex": celex, "items": len(records_)}, [], False
            try:
                source = json.dumps(sent, ensure_ascii=False)
                record["sentences"] = ask(client, systems["act"], sent, TASKS["act"], ACT_SCHEMA, "change_act_summary",
                                          lambda reply: validate_act(reply, source), log, 700)
            except ConnectionLost:
                raise
            except (Unclassified, OverflowError) as exc:
                record["failure"], failed = str(exc) or "too long", True
            except Exception as exc:  # noqa: BLE001
                record["failure"], failed = f"{type(exc).__name__}: {exc}"[:400], True
            record["requests"] = log
            write_json(out / "acts" / f"{safe_name(celex)}.json", record)
            progress.step(failed)
        return work

    try:
        ok, error = reachable(client)        # also tells the client the address and model name
        if not ok:
            wait_until_reachable(client, error)
        save = lambda: export(out, planned, args.language, args.link_template)  # noqa: E731
        run_until_done(client, "Changes", open_items, item_worker, workers, after_pass=save)
        run_until_done(client, "Acts", open_acts, act_worker, workers)
    except KeyboardInterrupt:
        export(out, planned, args.language, args.link_template)
        print("\nStopped by Ctrl+C. Run the same command to continue.", flush=True)
        return 130
    summary = export(out, planned, args.language, args.link_template)
    print(f"\nFinished: {summary['explained']:,} of {len(planned):,} items explained, {summary['failed']:,} failed. "
          f"{summary['in_inbox']:,} go to {summary['departments']:,} department inboxes "
          f"({summary['cards']:,} cards).")
    print(f"Open in a browser: {out / 'preview' / 'index.html'}\nTo check by hand: {out / 'review.csv'}\n"
          f"For the act pages: {out / 'act_pages'} ({summary['act_pages']:,} acts)")
    if summary["failed"]:
        print("Run the same command again to retry what failed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
