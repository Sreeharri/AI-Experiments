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
from export_eurlex_changes import act_roles as export_act_roles, root_of  # noqa: E402
from lr_ai.qwen_client import QwenClient  # noqa: E402
from summarize_eurlex_provisions import ask  # noqa: E402

PROMPTS = {"change": ROOT / "prompts/eurlex_change_explain.md",
           "corrigendum": ROOT / "prompts/eurlex_corrigendum_explain.md",
           "act": ROOT / "prompts/eurlex_change_act_summary.md"}
TASKS = {"change": "TASK: Explain what changed. Return the JSON object described in the instructions.",
         "corrigendum": "TASK: Explain the corrigendum. Return the JSON object described in the instructions.",
         "act": "TASK: Write the paragraph. Return the JSON object described in the instructions."}
DEFAULT_EXPORT = ROOT / "runs/eurlex_changes_export_v5"
DEFAULT_CLASSIFICATION = ROOT / "runs/eurlex_classification_v5"
DEFAULT_OUTPUT = ROOT / "runs/eurlex_changes_v9"
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
POINT = re.compile(r"\bpoint\s*\(([a-z])\)", re.I)
POINT_CLAUSE = re.compile(r"(?<=;)\s*\([a-z]\)\s*(?=in point\s*\()", re.I)
TEXT_BUDGET = 6000            # characters per text (old, new, instruction) sent to Qwen
LIMITS = {"headline": 140, "text": 800, "what": 80, "value": 300, "date": 60, "sentence": 300, "importance": 500,
          "to_confirm": 220}
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

def signature(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", (text or "").casefold())


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
            "effective_from": days[0] if len(days) == 1 and all(r.get("effective_from") for r in item["records"]) else None,
            "wording": best_wording(item["records"]),
            "instruction_locator": next((r.get("instruction_locator") for r in item["records"]
                                         if r.get("instruction_locator")), None),
            "change_ids": [r["change_id"] for r in item["records"] if r.get("change_id")],
            "changes": [{"change_id": r.get("change_id"), "provision": r.get("provision"),
                         "operation": r.get("operation") or first["relation"],
                         "effective_from": str(r["effective_from"])[:10] if r.get("effective_from") else None,
                         "effective_source": r.get("effective_source") or ("CHANGE" if r.get("effective_from") else "MISSING"),
                         "comparison_status": r.get("comparison_status"),
                         "old_wording": bool(r.get("old_text")), "new_wording": bool(r.get("new_text"))}
                        for r in item["records"]],
            "dates_differ": len(days) > 1,
            "correction_locations": [c.get("location") for c in first.get("corrections") or []],
            "correction_roots": [list(r) for r in dict.fromkeys(
                root for c in first.get("corrections") or [] for root in [root_of(c.get("location") or "")] if root)],
            "relationship_id": first.get("relationship_id"),
            "changing_document_id": first.get("changing_document_id"),
            "changed_document_id": first.get("changed_document_id"),
            "missing": missing_reason(item)}


def text_situation(item: dict) -> dict:
    """Which wordings are there for an item (decides what may be said)."""
    records = item["records"]
    instruction = " ".join(r.get("instruction") or "" for r in records)
    operations = {r.get("operation") or r["relation"] for r in records} | {records[0]["relation"]}
    relation = records[0]["relation"]
    # Removal only: every change deletes, or a repeal relation with "OTHER" (the provision is repealed).
    removal = all((r.get("operation") or relation) in REMOVALS or
                  ((r.get("operation") or "OTHER") == "OTHER" and relation in REMOVALS) for r in records)
    return {"old": any(r.get("old_text") for r in records), "new": any(r.get("new_text") for r in records),
            "instruction": bool(instruction.strip()), "pair": bool(WORD_PAIR.search(instruction)),
            "finals": bool(records[0].get("final_articles")), "operations": operations,
            "removal": removal, "trimmed": any(r.get("trimmed") for r in records)}


def needs_scope_review(item):
    return item["kind"] == "ACT" and item["records"][0]["relation"] == "IMPLICITLY_REPEALS"


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




# ------------------------------------------------------------------ helpers

ACT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["sentences"],
              "properties": {"sentences": {"type": "array", "minItems": 2, "maxItems": 4,
                                           "items": {"type": "string", "maxLength": LIMITS["sentence"]}}}}


def validate_act(reply, source):
    """Only the form: two to four sentences. The content is not checked."""
    sentences = reply.get("sentences") if isinstance(reply, dict) else None
    cleaned = [squash(s) for s in sentences or [] if isinstance(s, str) and squash(s)]
    if not cleaned:
        raise Unclassified("The reply holds no sentences.")
    return cleaned[:4]


def one_change(record):
    """Use the matching numbered instruction where the export repeats all point replacements."""
    points = POINT.findall(record.get("provision") or "")
    instruction = record.get("instruction") or ""
    if not points or len(set(POINT.findall(instruction))) < 2:
        return record
    point = points[-1].casefold()
    clauses = [line for line in POINT_CLAUSE.split(instruction)
               if [p.casefold() for p in POINT.findall(line)] == [point]]
    return {**record, "instruction": clauses[0]} if len(clauses) == 1 else {**record, "instruction": ""}


# ------------------------------------------------------------------ simple explanation (v2.0, v2.1)
#
# One request per single change. Qwen gets the texts that exist for this change and writes only an explanation of
# what is different. Nothing is checked or rejected: what Qwen writes is kept. What is missing is written as a
# remark by Python. Dates come from the database (the effective date of each change). Deletions and repeals are
# not sent to Qwen: they are shown as "gestrichen" / "aufgehoben" with their date.

CORE_VERSION = "v2.1"
EXPLANATION_LIMIT = 900
SIMPLE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["explanation"],
                 "properties": {"explanation": {"type": "string", "maxLength": EXPLANATION_LIMIT}}}
CASES = ("CHANGED", "ADDED", "REMOVED", "ACT", "CORRECTION", "IMPLICIT_REPEAL")
REMARKS = {
    "de": {"REMOVED_DELETE": "Gestrichen.", "REMOVED_REPEAL": "Aufgehoben.",
           "OLD_MISSING": "Alter Wortlaut nicht verfügbar: erklärt aus dem neuen Wortlaut.",
           "NEW_MISSING": "Neuer Wortlaut nicht verfügbar: erklärt aus der Änderungsvorschrift.",
           "NO_TEXT": "Kein Wortlaut verfügbar.", "NO_ANSWER": "Erklärung konnte nicht erstellt werden.",
           "SCOPE": "Mögliche stillschweigende Aufhebung: Umfang offen.",
           "NO_DATE": "Kein Datum in der Datenbank."},
    "en": {"REMOVED_DELETE": "Deleted.", "REMOVED_REPEAL": "Repealed.",
           "OLD_MISSING": "Old wording not available: explained from the new wording.",
           "NEW_MISSING": "New wording not available: explained from the amending instruction.",
           "NO_TEXT": "No wording available.", "NO_ANSWER": "The explanation could not be written.",
           "SCOPE": "Possible implicit repeal: scope open.", "NO_DATE": "No date in the database."},
}
TEXT_SHARE = 6000            # characters per text sent to Qwen
ARTICLE_SHARE = 5000         # characters of the whole article sent as background


# The amending act says the place is deleted, even where the database calls the operation OTHER or REPLACE.
DELETE_CLAUSE = re.compile(r"\b(?:is|are|shall be)\s+(?:hereby\s+)?(?:deleted|repealed|removed)\b|"
                           r"\b(?:wird|werden)\b[^;:]{0,120}?\b(?:gestrichen|aufgehoben)\b|\bentf(?:ä|a)llt\b|"
                           r"\bentfallen\b", re.I)
OTHER_CLAUSE = re.compile(r"\b(?:is|are|shall be)\s+(?:hereby\s+)?(?:replaced|inserted|added|substituted|"
                          r"renumbered|supplemented)\b|\bread as follows\b|\bshall read\b|\b(?:ersetzt|eingefügt|"
                          r"angefügt)\b|erhält folgende Fassung", re.I)
PLACE_TOKEN = re.compile(r"\(([a-z0-9]+)\)|\b(\d+[a-z]?)\b", re.I)


def deletes_place(instruction: str | None, place: str | None) -> bool:
    """True when the amending act's words delete this place: "(3) in Article 2, points 1, 2 and 19 are deleted",
    "Chapter III, containing Articles 15 to 29, is deleted", or in a mixed instruction the clause "(b) points (d)
    and (e) are deleted" for point (e)."""
    text = re.sub(r"[‘“„][^’”“]*[’”“]", " ", instruction or "")      # without the quoted new wording
    clauses = [re.sub(r"^\W*(?:\(\w+\)\s*)+", "", c) for c in re.split(r"[;:]", text)]
    deleting = [c for c in clauses if DELETE_CLAUSE.search(c)]
    if not deleting:
        return False
    other = [c for c in clauses if OTHER_CLAUSE.search(c)]
    if not other:
        return True
    tokens = PLACE_TOKEN.findall(place or "")
    if not tokens:
        return False
    bracket, number = tokens[-1]
    mark = rf"\({re.escape(bracket)}\)" if bracket else rf"(?<![\d.(]){re.escape(number)}(?![\d)])"
    has = lambda clause: re.search(mark, clause, re.I)
    return any(has(c) for c in deleting) and not any(has(c) for c in other)


def change_case(kind: str, record: dict, relation: str) -> str:
    operation = (record.get("operation") or relation or "OTHER").upper()
    if kind == "CORRIGENDUM":
        return "CORRECTION"
    if relation == "IMPLICITLY_REPEALS":
        return "IMPLICIT_REPEAL"
    if operation in {"DELETE", "REPEAL"} or relation == "REPEALS":
        return "REMOVED"
    if kind == "PROVISION" and operation not in ADDITIONS and deletes_place(record.get("instruction"),
                                                                           record.get("provision")):
        return "REMOVED"
    if operation in ADDITIONS:
        return "ADDED"
    if kind == "ACT":
        return "ACT"
    return "CHANGED"


def short(text: str | None, limit: int = TEXT_SHARE) -> str | None:
    text = squash(text or "")
    return (text[:limit] + " …") if len(text) > limit else (text or None)


def simple_request(item: dict, record: dict, case: str) -> tuple[dict | None, list[str]]:
    """(what is sent to Qwen, remark codes). None when there is nothing to explain from."""
    first = item["records"][0]
    sent = {"changed_act": {"celex": first["changed"]["celex"], "title": first["changed"]["title"]},
            "changing_act": {"celex": first["changing"]["celex"], "title": first["changing"]["title"]},
            "kind_of_change": case, "place": record.get("provision") or "the whole act",
            "operation": record.get("operation") or first["relation"]}
    remarks = []
    if case == "CORRECTION":
        corrections = [{"location": c["location"], "wrong": c["wrong"], "correct": c["correct"]}
                       for c in (record.get("corrections") or [])[:12]]
        if corrections:
            sent["corrections"] = corrections
        elif record.get("instruction"):
            sent["corrigendum_text"] = short(record["instruction"])
        else:
            return None, ["NO_TEXT"]
        return sent, remarks
    old, new = short(record.get("old_text")), short(record.get("new_text"))
    instruction = short(record.get("instruction"), 3000)
    if case == "ACT":
        finals = " ".join(f"{f['locator']}: {f['text']}" for f in record.get("final_articles") or [])
        if not instruction and not finals:
            return None, ["NO_TEXT"]
        if instruction:
            sent["instruction"] = instruction
        if finals:
            sent["final_articles"] = short(finals, 3000)
        return sent, remarks
    if case == "ADDED":
        background(sent, record)
        if new:
            sent["new_text"] = new
        elif instruction:
            sent["instruction"] = instruction
        else:
            return None, ["NO_TEXT"]
        return sent, remarks
    background(sent, record)
    # CHANGED: old and new text where both exist, otherwise what there is
    if old and new:
        sent["old_text"], sent["new_text"] = old, new
    elif new:
        sent["new_text"] = new
        remarks.append("OLD_MISSING")
    elif instruction:
        sent["instruction"] = instruction
        remarks.append("NEW_MISSING" if not old else "OLD_MISSING")
        if old:
            sent["old_text"] = old
    else:
        return None, ["NO_TEXT"]
    return sent, remarks


def background(sent: dict, record: dict) -> None:
    """The whole changed article (or annex) from the database, before the change and now, so that Qwen understands
    what the place is about. Only background: the explanation is about the change at `place`."""
    if record.get("heading"):
        sent["article_heading"] = record["heading"]
    for key in ("article_before", "article_now"):
        part = record.get(key)
        if isinstance(part, dict) and part.get("text"):
            sent[key] = {"version_date": part.get("version_date"), "text": short(part["text"], ARTICLE_SHARE)}


def ask_simple(client, system: str, sent: dict, task: str) -> tuple[str | None, dict]:
    """One request. Returns (explanation or None, what happened). Nothing is rejected: an answer that cannot be read
    as JSON is kept as raw text when it has any."""
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": f"{json.dumps(sent, ensure_ascii=False)}\n\n{task}"}]
    try:
        reply = client.chat(messages, thinking=False, stream=False, seed=42, sampling={"temperature": 0},
                            schema=SIMPLE_SCHEMA, schema_name="change_explanation", max_tokens=700)
    except Exception as exc:  # noqa: BLE001 - the request could not be sent at all
        raise ConnectionLost(str(exc)) from exc
    usage = reply.usage or {}
    step = {"seconds": round(getattr(reply, "seconds", 0.0) or 0.0, 3), "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"), "finish_reason": getattr(reply, "finish_reason", None)}
    if not reply.ok:
        if reply.error_kind == "context_limit":
            return None, {**step, "error": "context limit"}
        raise ConnectionLost(client.clean(reply.error or f"Model request failed ({reply.error_kind})."))
    candidate, error = reply.parse_json()
    text = candidate.get("explanation") if isinstance(candidate, dict) else None
    if not isinstance(text, str) or not squash(text):
        raw = squash(reply.content or "")
        return (raw[:EXPLANATION_LIMIT] if raw and not raw.startswith("{") else None), {**step, "error": error or "empty"}
    return squash(text), {**step, "error": None}


def change_title(line: dict, language: str) -> str:
    """A short title built by code: "Article 14 paragraph 1 ersetzt"."""
    label = OPERATION_LABEL[language].get(line["operation"], "") or KIND_LABEL[language].get(line["operation"], "")
    return " ".join(part for part in (line["place"], label) if part).strip() or line["operation"]


def explain(client, systems, item, log, routing=None, part_dir=None):
    """Explain every single change of an item. Returns (prompt name, explanation) with one line per change."""
    first = item["records"][0]
    relation = first["relation"]
    records = item["records"] if item["kind"] == "PROVISION" else item["records"][:1]
    lines, asked, failed = [], 0, 0
    for position, record in enumerate(records, 1):
        record = one_change(record) if item["kind"] == "PROVISION" else record
        case = change_case(item["kind"], record, relation)
        day = record.get("effective_from") or (first.get("valid_from") if item["kind"] != "PROVISION" else None)
        line = {"change_id": record.get("change_id"), "place": record.get("provision") or "",
                "operation": (record.get("operation") or relation or "OTHER").upper(), "case": case,
                "effective_from": str(day)[:10] if day else None,
                "effective_source": record.get("effective_source") or ("CHANGE" if day else "MISSING"),
                "explanation": None, "remarks": []}
        if not day and item["kind"] != "CORRIGENDUM":
            line["remarks"].append("NO_DATE")
        if case == "REMOVED" and line["operation"] not in {"DELETE", "REPEAL", "REPEALS"} and relation != "REPEALS":
            line["operation"] = "REPEAL" if re.search(r"\brepealed\b|\baufgehoben\b", record.get("instruction") or "",
                                                      re.I) else "DELETE"   # the amending act's own word
        if case == "REMOVED":
            line["remarks"].insert(0, "REMOVED_REPEAL" if line["operation"] in {"REPEAL", "REPEALS"} or
                                   relation == "REPEALS" else "REMOVED_DELETE")
            lines.append(line)
            continue
        if case == "IMPLICIT_REPEAL":
            line["remarks"].insert(0, "SCOPE")
            lines.append(line)
            continue
        sent, remarks = simple_request(item, record, case)
        line["remarks"] = remarks + line["remarks"]
        if sent is None:
            lines.append(line)
            continue
        checkpoint = part_dir / f"{position}.json" if part_dir is not None else None
        saved = read_json(checkpoint) if checkpoint is not None and checkpoint.is_file() else {}
        if saved.get("explanation"):
            line["explanation"] = saved["explanation"]
        else:
            name = "corrigendum" if case == "CORRECTION" else "change"
            text, step = ask_simple(client, systems[name], sent, TASKS[name])
            log.append(step)
            asked += 1
            line["explanation"] = text
            if checkpoint is not None:
                write_json(checkpoint, {"status": "OK" if text else "FAILED", "sent": sent, "explanation": text,
                                        "answer": step})
        if not line["explanation"]:
            line["remarks"].append("NO_ANSWER")
            failed += 1
        lines.append(line)
    explained = [line for line in lines if line["explanation"]]
    if failed and not explained:
        raise Unclassified("No explanation could be written for the changes of this item.")
    language = LANGUAGE
    for line in lines:
        line["remark_texts"] = [REMARKS[language][code] for code in line["remarks"]]
        line["title"] = change_title(line, language)
    outcome = "PARTIAL" if failed else ("EXPLAINED" if explained else "NO_EXPLANATION_NEEDED")
    days = sorted({line["effective_from"] for line in lines if line["effective_from"]})
    dates = [{"what": "gilt ab" if language == "de" else "applies from", "date": day, "source": "DATABASE"}
             for day in days]
    text = " ".join(f"{line['place']}: {line['explanation']}" if line["place"] and len(lines) > 1
                    else line["explanation"] for line in explained)
    name = "corrigendum" if item["kind"] == "CORRIGENDUM" else "change"
    return name, {"headline": "; ".join(dict.fromkeys(line["title"] for line in lines))[:200],
                  "what_changed" if item["kind"] != "CORRIGENDUM" else "what_was_wrong": text,
                  "changes": lines, "dates": dates, "facts": [], "outcome": outcome,
                  "missing_changes": [line["place"] or line["change_id"] for line in lines if "NO_ANSWER" in line["remarks"]]}


def failure_kind(message: str) -> str:
    return "NO_ANSWER" if message else "OTHER"


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
            "domain_title": top.get("primary_title"), "subject": top.get("subject"),
            "reason": top.get("primary_reason"), "matched": [locator for locator, _ in found]}


def route(facts, results, index):
    """Who gets a change, and why."""
    changed, changing = facts["changed"]["celex"], facts["changing"]["celex"]
    if facts["root"]:
        found = best(index[changed].get(tuple(facts["root"]), []))
        if found:
            return {**found, "route": "CHANGED_PROVISION"}
    for root in facts.get("correction_roots") or []:
        found = best(index[changed].get(tuple(root), []))
        if found:
            return {**found, "route": "CORRECTED_PROVISION"}
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


def locator_key(value):
    return re.sub(r"\s+", " ", (value or "").strip().casefold()).rstrip(".")


def exact_routing(facts, routing):
    if routing.get("route") == "CHANGED_PROVISION":
        positions = [row.get("provision") for row in facts.get("changes", [])]
    elif routing.get("route") == "CORRECTED_PROVISION":
        positions = facts.get("correction_locations") or []
    else:
        return False
    matched = {locator_key(value) for value in routing.get("matched", [])}
    # A root match can include a different paragraph, table or duplicate label.
    return bool(positions and all(locator_key(value) and locator_key(value) in matched for value in positions)
                and len(routing.get("matched", [])) == 1)


def review_reasons(record):
    facts, routing = record["facts"], record["routing"]
    explanation = record.get("explanation") or {}
    significance = explanation.get("significance") or explanation.get("affects")
    reasons = []
    if record.get("scope_review"):
        reasons.append("REPEAL_SCOPE_UNVERIFIED")
    elif not explanation:
        reasons.append("MISSING_CHANGE_EVIDENCE" if record.get("no_text") else "EXPLANATION_FAILED")
    if explanation.get("missing_changes"):
        reasons.append("PARTIAL_EXPLANATION")
    if significance == "UNCLEAR":
        reasons.append("EFFECT_UNCLEAR")
    if not exact_routing(facts, routing):
        reasons.append("PROVISION_RELEVANCE_UNVERIFIED")
    if not routing.get("departments"):
        reasons.append("NO_DEPARTMENT")
    if record["kind"] == "PROVISION":
        if any(c.get("effective_source") in {"RELATIONSHIP", "ENTRY_INTO_FORCE", "MISSING"}
               for c in facts.get("changes", [])):
            reasons.append("CHANGE_DATE_REVIEW_REQUIRED")
        for change in facts.get("changes", []):
            operation = change["operation"]
            has_pair = (change["old_wording"] or operation in ADDITIONS) and (
                change["new_wording"] or operation in REMOVALS)
            if not has_pair or change.get("comparison_status") not in {
                None, "REVIEWED_MAPPING", "MATCHED_STORED_EVIDENCE",
            }:
                reasons.append("WORDING_REVIEW_REQUIRED")
                break
    return reasons


def goes_to_inbox(record) -> bool:
    """Every change routed to a department (DIRECT or INDIRECT) is shown, with its explanation or its remark."""
    return (record.get("routing") or {}).get("level") in LEVELS and not record.get("scope_review")


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
    if facts["kind"] == "ACT" and facts["relation"] == "IMPLICITLY_REPEALS":
        source, target = facts["changing"]["celex"], facts["changed"]["celex"]
        if language == "de":
            return f"Mögliche stillschweigende Aufhebung von {target} durch {source}. Umfang und Datum offen."
        return f"Possible implicit repeal of {target} by {source}. Scope and date are unverified."
    label = KIND_LABEL[language].get(facts["relation"], facts["relation"])
    day = facts["effective_from"]
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


WORDING_NOTE = {"de": {"old": "Alter Wortlaut nicht verfügbar", "new": "Neuer Wortlaut nicht verfügbar"},
                "en": {"old": "Old wording not available", "new": "New wording not available"}}


def change_lines(record, language):
    """Every single change of an item, with its own place, date, date source and the UI's old/new wording."""
    texts = record["texts"] if isinstance(record.get("texts"), list) else [
        {"old": (record.get("texts") or {}).get("old"), "new": (record.get("texts") or {}).get("new")}]
    lines = []
    for index, change in enumerate(record["facts"].get("changes") or [{}]):
        text = texts[index] if index < len(texts) else {}
        operation = change.get("operation")
        notes = []
        if not text.get("old") and operation not in ADDITIONS:
            notes.append(WORDING_NOTE[language]["old"])
        if not text.get("new") and operation not in REMOVALS:
            notes.append(WORDING_NOTE[language]["new"])
        explained = ((record.get("explanation") or {}).get("changes") or [])
        done = next((line for line in explained if line.get("change_id") == change.get("change_id")), {})
        lines.append({**change, "operation_label": OPERATION_LABEL[language].get(operation, operation),
                      "old_text": (text.get("old") or "")[:4000], "new_text": (text.get("new") or "")[:4000],
                      "wording_notes": notes, "explanation": done.get("explanation"),
                      "remarks": done.get("remark_texts", [])})
    return lines


def entry(record, language):
    facts, explanation = record["facts"], record.get("explanation") or {}
    lines = change_lines(record, language)
    first = {"old": next((c["old_text"] for c in lines if c["old_text"]), ""),
             "new": next((c["new_text"] for c in lines if c["new_text"]), "")}
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
            "importance": explanation.get("importance", ""),
            "to_confirm": explanation.get("to_confirm", ""),
            "review_reasons": review_reasons(record), "outcome": item_outcome(record),
            "missing_parts": explanation.get("missing_changes", []), "parts": explanation.get("parts", []),
            "changes": lines, "dates_differ": facts.get("dates_differ", False),
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
    """A preview of the department card: per change the place, what was done, the date from the database, Qwen's
    explanation and the remarks; the old and new wording behind a toggle."""
    e = html.escape
    out = [f'<div class="card"><h2>{e(card["title"] or card["celex"])}</h2>'
           f'<div class="meta">{e(card["celex"])}{" · " + e(", ".join(card["role"]["labels"])) if card.get("role") else ""}'
           f' · <a href="{e(card["eurlex_url"])}">EUR-Lex</a></div>']
    for change in card["changes"]:
        out.append(f'<div class="change"><span class="tag {e(change["label"])}">{e(change["label"])}</span>'
                   f'<span class="meta">{e(change["changing"]["celex"])} {e(change["changing"]["title"][:120])}</span>')
        lines = change.get("changes") or []
        for line in lines:
            out.append(f'<div class="fact"><b>{e(line.get("provision") or "")}</b> · {e(line.get("operation_label") or "")}'
                       f'{" · ab " + e(line["effective_from"]) if line.get("effective_from") else ""}</div>')
            if line.get("explanation"):
                out.append(f'<p>{e(line["explanation"])}</p>')
            for remark in line.get("remarks") or []:
                out.append(f'<div class="note">{e(remark)}</div>')
        if not lines and change.get("text"):
            out.append(f'<p>{e(change["text"])}</p>')
        if not lines and not change.get("text"):
            out.append(f'<p>{e(change["headline"])}</p>')
        if any(line["old_text"] or line["new_text"] for line in lines):
            out.append(f'<details><summary>{"Wortlaut vorher / nachher" if language == "de" else "Old / new wording"}'
                       '</summary>' + "".join(f'<p class="meta">{e(line.get("provision") or "")}</p>'
                                              f'<pre>{e(line["old_text"])}</pre><pre>{e(line["new_text"])}</pre>'
                                              for line in lines if line["old_text"] or line["new_text"])
                       + '</details>')
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
            "importance": explanation.get("importance") or "",
            "change_ids": facts.get("change_ids", []), "relationship_id": facts.get("relationship_id"),
            "changing_document_id": facts.get("changing_document_id"),
            "changed_document_id": facts.get("changed_document_id"), "link": link_for(template, facts)}


def act_roles(records: list[dict], export_dir: Path | None = None) -> dict[str, dict]:
    """Role badges of every act. From the export's roles.json (built from ALL relationships in the database),
    else from the records. Independent flags: changes_others, was_changed, was_corrected, corrigendum."""
    path = Path(export_dir) / "roles.json" if export_dir else None
    if path and path.is_file():
        return read_json(path)
    return export_act_roles([{"relation_type": r["relation"], "changing_celex": r["changing"]["celex"],
                              "changed_celex": r["changed"]["celex"]} for r in records])


ROLE_WORDS_EN = {"Ändert andere Rechtsakte": "Changes other acts", "Wurde geändert": "Was changed",
                 "Wurde berichtigt": "Was corrected", "Berichtigung": "Corrigendum"}


def role_of(roles, celex, language):
    role = (roles or {}).get(celex)
    if not role:
        return None
    labels = role.get("labels") or []
    return {**{k: v for k, v in role.items() if k != "labels"},
            "labels": labels if language == "de" else [ROLE_WORDS_EN.get(label, label) for label in labels]}


def act_pages(out, done, acts, language, template="", roles=None):
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
        page = {"celex": celex, "title": titles.get(celex, ""), "role": role_of(roles, celex, language),
                "changes_made": None, "changed_by": None, "note": NOTE[language]}
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
            + (f'<p class="note">{e(", ".join(i["review_reasons"]))}</p>' if i.get("review_reasons") else "")
            + (f'<a href="{e(i["link"])}">{e(words["open"])}</a>' if i["link"] else "") + "</div>" for i in items)

    parts = [f'<!doctype html><html lang="{language}"><head><meta charset="utf-8"><title>{e(page["celex"])}</title>'
             f'<style>{STYLE}</style></head><body><p><a href="index.html">&larr;</a></p>'
             f'<h1>{e(page["celex"])}</h1><p>{e(page["title"])}</p>'
             + ("<p>" + "".join(f'<span class="tag">{e(label)}</span>' for label in page["role"]["labels"]) + "</p>"
                if page.get("role") else "")]
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


def review_dates(item, changing, language):
    if item["kind"] in {"ACT", "CORRIGENDUM"}:
        if item.get("effective_from"):
            return item["effective_from"]
        label = "Datum des Rechtsakts" if language == "de" else "Date of act"
        return f"{label} {changing['date']}" if changing.get("date") else ""
    return " | ".join(f"{c.get('provision') or '—'} {c.get('effective_from') or '—'} ({c['effective_source']})"
                      for c in item["changes"])


def export(out, planned, language, template="", roles=None):
    records = []
    for item in planned:
        path = out / "items" / f"{item['id']}.json"
        if path.is_file():
            records.append(read_json(path))
    done = [r for r in records if "explanation" in r or r.get("no_text") or r.get("scope_review")]
    failed = [r for r in records if "failure" in r]
    acts, act_failures = {}, []
    for path in (out / "acts").glob("*.json") if (out / "acts").is_dir() else []:
        saved = read_json(path)
        acts[saved["celex"]] = saved.get("sentences", [])
        if saved.get("failure"):
            act_failures.append({"celex": saved["celex"], "error": saved["failure"]})
    inbox = defaultdict(lambda: defaultdict(list))
    rows, to_screen = [], []
    titles = {r["facts"]["changed"]["celex"]: r["facts"]["changed"]["title"] for r in done}
    for record in done:
        item = entry(record, language)
        if item["review_reasons"]:
            to_screen.append(record)
        if goes_to_inbox(record):
            for department in record["routing"]["departments"] or ["(no department yet)"]:
                inbox[department][record["facts"]["changed"]["celex"]].append(item)
        rows.append([record["facts"]["changed"]["celex"], item["provision"] or "", record["facts"]["changing"]["celex"],
                     item["label"], ", ".join(item["operations"]), item["effective_from"] or "", item["wording"],
                     item["outcome"], item["significance"] or "", item["level"] or "", record["routing"]["route"],
                     ", ".join(record["routing"]["departments"]), item["headline"], item["text"],
                     " | ".join(f"{f['what']}: {f['before']} -> {f['after']}" for f in item["facts"]),
                     " | ".join(f"{d['what']}: {d['date']}" for d in item["dates"]),
                     f"{item['importance']} {item['to_confirm']}".strip(),
                     review_dates(item, record["facts"]["changing"], language),
                     (item["old_text"] or "")[:300], (item["new_text"] or "")[:300]])
    cards_total = 0
    (out / "preview").mkdir(parents=True, exist_ok=True)
    index = []
    for department, by_act in sorted(inbox.items()):
        cards = []
        for celex, changes in sorted(by_act.items()):
            changes.sort(key=lambda c: (c["effective_from"] or "", c["provision"] or ""), reverse=True)
            for change in changes:
                change["changing"]["role"] = role_of(roles, change["changing"]["celex"], language)
            cards.append({"celex": celex, "title": titles.get(celex, ""), **act_links(celex),
                          "role": role_of(roles, celex, language), "changes": changes, "changing_acts": {source: [c["headline"] for c in changes if c["changing"]["celex"] == source]
                                            for source in dict.fromkeys(c["changing"]["celex"] for c in changes)},
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
                         "outcome", "significance", "level", "route", "departments", "headline", "explanation", "facts",
                         "dates", "importance for Max Bögl", "changes with date (source)", "old_start", "new_start", "explanation correct (yes/no)", "missing or wrong",
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
    to_screen.extend(failed)
    with (out / "to_screen.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["changed_act", "provision", "changing_act", "relation", "route", "significance", "headline", "item_id", "change_ids", "review_reasons"])
        for record in to_screen:
            ex = record.get("explanation") or {}
            writer.writerow([record["facts"]["changed"]["celex"], record["facts"]["provision"] or "",
                             record["facts"]["changing"]["celex"], record["facts"]["relation"],
                             record["routing"]["route"], ex.get("significance") or ex.get("affects") or "",
                             ex.get("headline", "") or code_line(record["facts"], language),
                             record["facts"]["id"], ", ".join(record["facts"]["change_ids"]),
                             ", ".join(review_reasons(record))])
    summary = {"language": language, "items_planned": len(planned), "explained": sum("explanation" in r for r in done), "failed": len(failed),
               "missing_evidence": sum(bool(r.get("no_text")) for r in done),
               "scope_review": sum(bool(r.get("scope_review")) for r in done),
               "review_queue": len(to_screen), "act_summary_failures": act_failures,
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
    summary.update(outcome_counts(out, planned, records))
    summary["act_pages"] = act_pages(out, done, acts, language, template, roles)
    write_json(out / "summary.json", summary)
    return summary


def item_outcome(record: dict) -> str:
    if record.get("outcome"):
        return record["outcome"]
    if record.get("scope_review"):
        return "SCOPE_REVIEW"
    if record.get("no_text"):
        return "MISSING_EVIDENCE"
    if "failure" in record:
        return "FAILED"
    return (record.get("explanation") or {}).get("outcome", "EXPLAINED") if record.get("explanation") else "OPEN"


def outcome_counts(out: Path, planned, records) -> dict:
    """Separate outcomes per item (EXPLAINED, PARTIAL, FAILED, MISSING_EVIDENCE, SCOPE_REVIEW, OPEN) and per part,
    and what kind of error made the failed parts fail."""
    items = Counter(item_outcome(r) for r in records)
    items["OPEN"] += len(planned) - len(records)
    parts, kinds, diagnosable = Counter(), Counter(), []
    for item in planned:
        folder = out / "parts" / item["id"]
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            part = read_json(path)
            parts[part.get("status", "OLD_FORMAT")] += 1
            if part.get("status") == "FAILED":
                kinds[part.get("kind") or failure_kind(part.get("error", ""))] += 1
                diagnosable.append(str(path.relative_to(out)))
    return {"outcomes": {k: items[k] for k in ("EXPLAINED", "NO_EXPLANATION_NEEDED", "PARTIAL", "FAILED",
                                                "MISSING_EVIDENCE", "SCOPE_REVIEW", "OPEN")},
            "parts": dict(parts), "failed_part_kinds": dict(kinds.most_common()),
            "failed_part_files": diagnosable[:200]}


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
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--sample", type=int, help="Explain only this many items (a fixed mix of kinds).")
    selection.add_argument("--item-ids", type=Path, help="Replay these item IDs, one per line.")
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
    roles = act_roles(records, args.export_dir)
    if args.celex:
        wanted = {c.strip().upper() for c in args.celex}
        items = [i for i in items if {i["records"][0]["changed"]["celex"], i["records"][0]["changing"]["celex"]} & wanted]
    index = locator_index(results)
    print(f"{len(records):,} change records, {len(items):,} items to explain: "
          f"{dict(Counter(i['kind'] for i in items))}", flush=True)
    routes = Counter(route(item_facts(i), results, index)["route"] for i in items)
    print(f"Routing: {dict(routes)}", flush=True)
    planned = pick_sample(items, args.sample) if args.sample else items
    if args.item_ids:
        try:
            ids = args.item_ids.read_text(encoding="utf-8-sig").split()
        except OSError as exc:
            parser.error(str(exc))
        by_id = {item["id"]: item for item in items}
        if not ids or len(ids) != len(set(ids)):
            parser.error("--item-ids must contain distinct IDs")
        missing = set(ids) - by_id.keys()
        if missing:
            parser.error(f"{len(missing)} requested item IDs are absent from the export")
        planned = [by_id[item_id] for item_id in ids]
    if args.count_only:
        print(f"Planned: {len(planned):,} items.", flush=True)
        return 0
    if not items:
        print("Nothing to explain.", file=sys.stderr)
        return 1
    fingerprint = sha([systems, TASKS, SIMPLE_SCHEMA, ACT_SCHEMA, args.language, CORE_VERSION, planned, results,
                       args.link_template])
    config = out / "run_config.json"
    if config.is_file() and read_json(config).get("fingerprint") != fingerprint:
        print("The inputs or settings changed since this folder was started. Use a new --output-dir.",
              file=sys.stderr)
        return 1
    write_json(config, {"fingerprint": fingerprint, "language": args.language, "export": str(args.export_dir),
                        "system_prompts": systems})
    print(f"Planned: {len(planned):,} items.", flush=True)

    client = None
    workers = max(1, min(args.workers, MAX_WORKERS))

    def saved(item):
        path = out / "items" / f"{item['id']}.json"
        record = read_json(path) if path.is_file() else {}
        return record if ("explanation" in record or record.get("no_text") or record.get("scope_review")) else None

    def open_items():
        """Items still to ask: never asked, failed, or PARTIAL (then only their failed parts are asked again)."""
        todo = [item for item in planned if (record := saved(item)) is None or
                (record.get("explanation") or {}).get("outcome") == "PARTIAL" and item["id"] not in retried]
        return todo, len(planned) - len(todo)

    retried = set()                    # PARTIAL items get one new try per start

    def item_worker(progress):
        def work(item):
            facts = item_facts(item)
            record = {"kind": item["kind"], "relation": facts["relation"], "facts": facts,
                      "routing": route(facts, results, index),
                      "texts": [{"change_id": r.get("change_id"), "old": r.get("old_text"), "new": r.get("new_text")}
                                for r in item["records"]]}
            failed = False
            retried.add(item["id"])
            if needs_scope_review(item):
                record["scope_review"], record["outcome"] = True, "SCOPE_REVIEW"
            elif facts["missing"]:
                record["no_text"] = True       # nothing to explain: a line built by code, listed in missing_text.csv
                record["outcome"] = "MISSING_EVIDENCE"
            else:
                log = []
                try:
                    record["prompt"], record["explanation"] = explain(
                        client, systems, item, log, record["routing"], out / "parts" / item["id"])
                except ConnectionLost:
                    raise
                except Unclassified as exc:
                    record["failure"], failed = str(exc), True
                except Exception as exc:  # noqa: BLE001 - one bad record must not stop the run
                    record["failure"], failed = f"{type(exc).__name__}: {exc}"[:400], True
                record["requests"] = log
                record["outcome"] = "FAILED" if failed else record["explanation"].get("outcome", "EXPLAINED")
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
                                 "what_changed": (r["explanation"].get("what_changed") or
                                                  r["explanation"].get("what_was_wrong") or "")[:1000],
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
        pending, _ = open_items()
        if open_acts()[0] or any(not needs_scope_review(item) and missing_reason(item) is None for item in pending):
            client = QwenClient.from_env(args.env_file, retries=1)
            ok, error = reachable(client)
            if not ok:
                wait_until_reachable(client, error)
        save = lambda: export(out, planned, args.language, args.link_template, roles)  # noqa: E731
        run_until_done(client, "Changes", open_items, item_worker, workers, after_pass=save)
        run_until_done(client, "Acts", open_acts, act_worker, workers)
    except KeyboardInterrupt:
        export(out, planned, args.language, args.link_template, roles)
        print("\nStopped by Ctrl+C. Run the same command to continue.", flush=True)
        return 130
    summary = export(out, planned, args.language, args.link_template, roles)
    print(f"\nFinished: {summary['explained']:,} of {len(planned):,} items explained, {summary['failed']:,} failed. "
          f"{summary['in_inbox']:,} go to {summary['departments']:,} department inboxes "
          f"({summary['cards']:,} cards).")
    print(f"Open in a browser: {out / 'preview' / 'index.html'}\nTo check by hand: {out / 'review.csv'}\n"
          f"For the act pages: {out / 'act_pages'} ({summary['act_pages']:,} acts)")
    print(f"{summary['missing_evidence']:,} missing evidence, {summary['scope_review']:,} repeal scope reviews, "
          f"{summary['review_queue']:,} total items in the review queue.")
    if summary["failed"] or summary["act_summary_failures"] or summary["outcomes"]["PARTIAL"]:
        print("Run the same command again to retry what failed.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
