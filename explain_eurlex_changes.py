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
DEFAULT_OUTPUT = ROOT / "runs/eurlex_changes_v7"
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
                       r"verkürzt|verlängert|konkretisier\w*|präzisier\w*|clarif\w*|anstatt|statt|gesenkt|erhöht|previously|no longer|instead of|extended|"
                       r"tightened|relaxed|reduced|increased)\b", re.I)
PROLONGING = re.compile(r"prolong|extend|extension|postpon|defer|validity|verlänger|verschieb|Geltungsdauer|"
                        r"Gültigkeit", re.I)
# Without new wording a removal must not be told as something new.
NEW_CLAIM = re.compile(r"\bneue[nrsm]?\b|\bneu\b|eingeführt|hinzugefügt|eingefügt|\bnew\b|introduc|added", re.I)
# The importance part explains, it does not advise: tasks come in the next phase.
ADVICE = re.compile(r"\bsollten?\b|empfehl|empfohlen|ratsam|Handlungsbedarf|prüfen Sie|zu prüfen|\bshould\b|"
                    r"recommend|advis", re.I)
PASSAGE_MARK = re.compile(r"\[[A-Z]\d+\]", re.I)
REPEAL_TERM = re.compile(r"\b(?:rücktritt|zurückgetreten|withdrawal|resignation)\b", re.I)
REPLACEMENT_PLACEHOLDER = re.compile(r"\b(?:not used|nicht verwendet|nicht belegt)\b", re.I)
BAD_PAINT_TERM = re.compile(r"\b(?:innen- und )?außentiegel\b", re.I)
POINT = re.compile(r"\bpoint\s*\(([a-z])\)", re.I)
POINT_CLAUSE = re.compile(r"(?<=;)\s*\([a-z]\)\s*(?=in point\s*\()", re.I)
CONTRACT_TERMS = (
    re.compile(r"\b(?:bauauftr\w*|bauleistungen|works contracts?)\b", re.I),
    re.compile(r"\b(?:dienstleistungs\w*|services?|service contracts?)\b", re.I),
)
CURRENCY_REPLACEMENT = re.compile(
    r"[‘’'\"“”](EUR [\d\s.,]+)[‘’'\"“”]\s+is replaced by\s+[‘’'\"“”](EUR [\d\s.,]+)[‘’'\"“”]", re.I)
COMPANY_DUTY = re.compile(r"\bMax Bögl\s+(?:muss|darf nicht|ist verpflichtet|must|is required)\b", re.I)
DATE_PURPOSES = (
    (r"umsetz|transpos", r"transpos|bring into force the laws|laws.*(?:comply|necessary)|comply.*directive"),
    (r"bericht|report", r"report"),
    (r"inkraft|in kraft|entry into force", r"entry into force|enter into force|eintritt in kraft"),
    (r"gilt ab|anwendung|appl", r"appl|(?:change|annotation):"),
    (r"erlass|annahme|adopt|act date", r"date of the changing act|adopt"),
)
TEXT_BUDGET = 6000            # characters per text (old, new, instruction) sent to Qwen
MAX_CHANGES_LISTED = 25       # changes of one provision listed in one request (big annexes have hundreds)
PASSAGE_CHARS = 450
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

def passages_of(text: str | None, mark: str, budget: int = TEXT_BUDGET, keep: set[str] | None = None, start: int = 1):
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
        key = f"{mark}{len(passages) + start}"
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


BUSINESS_AREAS: dict[str, list[str]] = {}     # legal area -> Max Bögl business areas (business anchors), from main()
# Routes precise enough to say why a change matters: the changed or changing provision itself, or the whole
# act for a whole-act change or a corrigendum. A route only via the changing act is too broad.
PRECISE_ROUTES = {"CHANGED_PROVISION", "CHANGING_PROVISION", "CORRECTED_PROVISION"}


def precise(routing: dict | None, kind: str) -> bool:
    route_ = (routing or {}).get("route")
    return route_ in PRECISE_ROUTES or (route_ == "CHANGED_ACT" and kind in {"ACT", "CORRIGENDUM"})


def load_business_areas(path: Path) -> dict[str, list[str]]:
    """Labels of the business anchors per legal area (read only)."""
    try:
        import yaml
        anchors = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("anchors") or {}
    except Exception:  # noqa: BLE001 - without the file the importance just has no business areas
        return {}
    areas = defaultdict(list)
    for anchor in anchors.values():
        if isinstance(anchor, dict) and anchor.get("domain") and anchor.get("label"):
            areas[anchor["domain"]].append(anchor["label"])
    return dict(areas)


def relevance_part(routing: dict | None, kind: str = "PROVISION") -> tuple[str, dict]:
    """Why the affected provision matters for Max Bögl, from the classification (mark K1). Empty when the
    change is not matched to a DIRECT or INDIRECT provision (or whole act): then no importance is written."""
    if not routing or routing.get("level") not in LEVELS or not precise(routing, kind):
        return "", {}
    bits = [f"Classified {routing['level']} for Max Bögl"]
    if routing.get("domain_title"):
        bits.append(f"legal area: {routing['domain_title']}")
    if routing.get("subject"):
        bits.append(f"subject: {routing['subject']}")
    if routing.get("reason"):
        bits.append(f"reason: {routing['reason']}")
    if routing.get("departments"):
        bits.append(f"departments: {', '.join(routing['departments'])}")
    if BUSINESS_AREAS.get(routing.get("domain")):
        bits.append(f"Max Bögl business areas in this legal area: {', '.join(BUSINESS_AREAS[routing['domain']][:6])}")
    text = "; ".join(bits) + "."
    return f"[K1] {text}", {"K1": text}


# ------------------------------------------------------------------ helpers kept from v1.6

def importance_schema(passage_ids, limit="importance"):
    """Only with a classification (K1): otherwise the importance must stay empty."""
    return {"type": "string", "maxLength": LIMITS[limit] if "K1" in passage_ids else 0}


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


def check_public_text(texts, operations=()):
    for value in texts:
        if PASSAGE_MARK.search(value):
            raise Unclassified("Keep passage markers in the evidence fields only.")
        if BAD_PAINT_TERM.search(value):
            raise Unclassified("Translate paints and varnishes as Farben und Lacke.")
        if set(operations) & REMOVALS and REPEAL_TERM.search(value):
            raise Unclassified("Use Aufhebung (repeal) for the removal of a provision or act.")


def check_importance(reply, passages, every) -> tuple[str, str]:
    """(importance, to_confirm). Why the change matters for Max Bögl: required with a classification (K1),
    empty without; no advice. to_confirm: what is still open before anyone acts (may be empty)."""
    value = squash(reply.get("importance")) if isinstance(reply.get("importance"), str) else ""
    confirm = squash(reply.get("to_confirm")) if isinstance(reply.get("to_confirm"), str) else ""
    if "K1" not in passages:
        if value or confirm:
            raise Unclassified("importance and to_confirm must stay empty: the change is not matched to a "
                               "classified provision.")
        return "", ""
    if len(confirm) > LIMITS["to_confirm"]:
        raise Unclassified(f"to_confirm is one short sentence (up to {LIMITS['to_confirm']} characters).")
    if confirm:
        check([confirm], every, "to_confirm")
    if not value or len(value) > LIMITS["importance"]:
        raise Unclassified(f"importance must be one or two sentences (up to {LIMITS['importance']} characters).")
    if COMPANY_DUTY.search(f"{value} {confirm}"):
        raise Unclassified("Describe the business connection conditionally. Do not assert a company obligation.")
    if word := ADVICE.search(value):
        raise Unclassified(f"importance explains, it does not advise ('{word.group(0)}'). Say why it matters.")
    check([value], every, "importance")
    return value, confirm


def validate_act(reply, source):
    sentences = reply.get("sentences") if isinstance(reply, dict) and set(reply) == {"sentences"} else None
    if not isinstance(sentences, list) or not 2 <= len(sentences) <= 4:
        raise Unclassified("The reply holds exactly sentences, a list of two to four full sentences.")
    cleaned = [squash(s) for s in sentences if isinstance(s, str) and squash(s)]
    if len(cleaned) != len(sentences) or any(len(s) > LIMITS["sentence"] for s in cleaned):
        raise Unclassified(f"Every sentence is a text of 1 to {LIMITS['sentence']} characters.")
    check_public_text(cleaned)
    check(cleaned, source, "sentences")
    check_language(cleaned)
    return cleaned


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


# ------------------------------------------------------------------ evidence units (v1.7)
#
# One request to Qwen covers one unit: one single change (PROVISION), one whole-act change (ACT) or one corrigendum.
# Python builds the evidence: every passage gets an ID that is unique within the unit and is saved with the change
# ID, the exact place and its type, so the stable reference is "<change id>:<passage id>". Qwen writes the German
# text and only SELECTS passage IDs. Python copies the quotations from the selected passages, word for word.
# Where old and new wording are both given, only the sentences that differ can be selected as evidence of a
# difference, so an unchanged neighbouring sentence can never support a claimed change.

CORE_VERSION = "v1.7"
EVIDENCE_KINDS = {"O": "OLD", "N": "NEW", "I": "INSTRUCTION", "F": "FINAL_ARTICLES", "M": "ACT_DATES",
                  "W": "WRONG", "R": "CORRECT", "T": "CORRIGENDUM_TEXT", "C": "PLACE_CONTEXT", "K": "RELEVANCE"}
SITUATIONS = {
    "BOTH": "Old and new wording of this place are given. Explain the difference; cite changed passages only.",
    "NEW_ONLY": "Only the new wording is given. Say what the place says now. Compare with nothing.",
    "INSTRUCTION": "Only the amending instruction is given. Report exactly what the instruction establishes.",
    "REMOVAL_OLD": "The place is deleted. The old wording is given. Say what no longer applies. Nothing is new.",
    "REMOVAL_INSTRUCTION": "The place or act is deleted or repealed by the instruction. Report what is removed and "
                           "from when. Nothing is new, and do not guess the removed content.",
    "ACT": "A change of the whole act. Report exactly what the instruction and final articles establish.",
    "CORRECTION": "A corrigendum: wrong and correct wording are given.",
}
DATE_KINDS = {"TRANSPOSITION": "Umsetzungsfrist", "TRANSITION_END": "Übergangsfrist bis", "REPORTING": "Berichtsfrist",
              "APPLIES_FROM": "gilt ab", "OTHER_DEADLINE": "Frist"}
DATE_KINDS_EN = {"TRANSPOSITION": "Transposition deadline", "TRANSITION_END": "Transition ends",
                 "REPORTING": "Reporting deadline", "APPLIES_FROM": "Applies from", "OTHER_DEADLINE": "Deadline"}
DATE_SUPPORT = {"TRANSPOSITION": r"transpos|bring into force|comply with this|Umsetzung|nachzukommen",
                "TRANSITION_END": r"until|transitional|derogation|continue to apply|bis zum|Übergang",
                "REPORTING": r"report|Bericht", "APPLIES_FROM": r"appl|gilt|anzuwenden",
                "OTHER_DEADLINE": r"\bby\b|no later than|before|deadline|spätestens|bis"}
AUTO_DATES = {"de": {"CHANGE": "gilt ab", "ANNOTATION": "gilt ab", "ENTRY_INTO_FORCE": "Inkrafttreten des Änderungsakts",
                     "ADOPTION": "Datum des Änderungsakts", "RELATIONSHIP": "gilt ab (laut Verknüpfung, prüfen)"},
              "en": {"CHANGE": "Applies from", "ANNOTATION": "Applies from",
                     "ENTRY_INTO_FORCE": "Entry into force of the amending act", "ADOPTION": "Date of the amending act",
                     "RELATIONSHIP": "Applies from (from the relationship, to check)"}}
# Contract categories in public procurement: a value may only be tied to a category the place itself names.
CATEGORY_TERMS = {
    "works": re.compile(r"\b(?:works?(?: contracts?)?|Bauauftr\w*|Bauleistung\w*|Bauarbeiten|Werkvertr\w*|"
                        r"Werkeverträg\w*|Werkleistung\w*)\b", re.I),
    "services": re.compile(r"\b(?:services?(?: contracts?)?|Dienstleistung\w*)\b", re.I),
    "supplies": re.compile(r"\b(?:suppl(?:y|ies)(?: contracts?)?|Lieferauftr\w*|Lieferung\w*|Lieferleistung\w*)\b", re.I),
}
PLACE_REFERENCE = re.compile(
    r"\b(?:Art\.?|Artikel|Article|Absatz|Abs\.|Unterabsatz|paragraph|subparagraph|Nummer|Nr\.|Punkt|point|"
    r"Buchstabe|Anhang|Annex|Anlage|Teil|Part|Kapitel|Chapter)\s+[0-9IVXLC]+[a-z]?(?:\s*\(\w+\))*", re.I)
MAX_FACTS = 5
FACT_EVIDENCE = 3


class PartFailed(Exception):
    """A unit could not be explained; `trace` holds what was sent, every answer and every error."""

    def __init__(self, message, trace):
        super().__init__(message)
        self.trace = trace


def sentences_of(text: str | None) -> list[str]:
    return [s for s in re.split(r"(?<=[.;:])\s+(?=[A-Z(‘“0-9])", squash(text or "")) if s]


def changed_flags(old: str | None, new: str | None) -> tuple[list[bool], list[bool]]:
    """Which sentences of the old and the new wording differ (True) from the other side."""
    import difflib
    a, b = sentences_of(old), sentences_of(new)
    flags_a, flags_b = [False] * len(a), [False] * len(b)
    matcher = difflib.SequenceMatcher(None, [signature(s) for s in a], [signature(s) for s in b], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            for i in range(i1, i2):
                flags_a[i] = True
            for j in range(j1, j2):
                flags_b[j] = True
    return flags_a, flags_b


def evidence_passages(text: str | None, prefix: str, flags: list[bool] | None = None,
                      budget: int = TEXT_BUDGET) -> list[dict]:
    """Numbered passages of one text. With flags, every passage is either all changed or all unchanged, and over
    the budget only the changed passages and one neighbour on each side are kept."""
    parts = sentences_of(text)
    if not parts:
        return []
    flags = flags if flags is not None and len(flags) == len(parts) else [None] * len(parts)
    groups = []
    for sentence, flag in zip(parts, flags):
        if groups and groups[-1]["changed"] == flag and len(groups[-1]["text"]) + len(sentence) <= PASSAGE_CHARS:
            groups[-1]["text"] += " " + sentence
        else:
            groups.append({"text": sentence, "changed": flag})
    chosen = set(range(len(groups)))
    if sum(len(g["text"]) for g in groups) > budget and any(g["changed"] for g in groups):
        chosen = {j for i, g in enumerate(groups) if g["changed"] for j in (i - 1, i, i + 1) if 0 <= j < len(groups)}
    found, used = [], 0
    for index, group in enumerate(groups):
        if index not in chosen or used + len(group["text"]) > budget:
            continue
        used += len(group["text"])
        found.append({"id": f"{prefix}{len(found) + 1}", "kind": EVIDENCE_KINDS[prefix], "text": group["text"],
                      "changed": group["changed"]})
    return found


def point_letter(place: str | None) -> str | None:
    letters = re.findall(r"\bpoint\s*\(?([a-z])\)?(?![a-z0-9])", place or "", re.I)
    return letters[-1].casefold() if letters else None


def point_subject(text: str | None, letter: str | None) -> str | None:
    """The subject a point is about, from its own wording: "(b) service contracts subsidised ..." -> the words up to
    the first clause break. None when the point's wording is not given."""
    if not text or not letter:
        return None
    match = re.search(rf"(?:^|\s)\({re.escape(letter)}\)\s+(.{{3,200}}?)(?=,|;|:|\s(?:which|that|where|whose|"
                      rf"if|provided)\s|$)", squash(text), re.I)
    return squash(match.group(1)) if match else None


def point_subject_clause(unit: dict) -> str:
    """The whole wording of the point, from its own clause in the context or the new/old text."""
    letter = unit["letter"]
    for passage in unit["evidence"].values():
        if passage["id"][0] in "CNO":
            match = re.search(rf"\({re.escape(letter)}\)\s+(.*?)(?=\s\([a-z]\)\s|$)", passage["text"], re.S)
            if match:
                return match.group(1)
    return ""


def categories_in(text: str) -> set[str]:
    return {name for name, pattern in CATEGORY_TERMS.items() if pattern.search(text or "")}


def without_places(value: str) -> str:
    """A value without its place references ("Absatz 2", "point (b)"): their numbers are not values."""
    return PLACE_REFERENCE.sub(" ", value or "")


def unit_situation(kind: str, record: dict, operations: set[str]) -> str:
    if kind == "CORRIGENDUM":
        return "CORRECTION"
    removal = bool(operations & REMOVALS) or (record.get("operation") or "OTHER") in REMOVALS
    if kind == "ACT":
        return "REMOVAL_INSTRUCTION" if removal else "ACT"
    old, new, instruction = record.get("old_text"), record.get("new_text"), record.get("instruction")
    if removal:
        return "REMOVAL_OLD" if old else "REMOVAL_INSTRUCTION"
    if old and new:
        return "BOTH"
    if new:
        return "NEW_ONLY"
    return "INSTRUCTION" if instruction else "NEW_ONLY"


def build_unit(item: dict, record: dict, routing: dict | None) -> dict:
    """Everything one request needs: the evidence passages (with IDs and types), what Qwen may select for
    which field, the Python-built dates, and the JSON sent to Qwen."""
    first = item["records"][0]
    kind = item["kind"]
    record = one_change(record) if kind == "PROVISION" else record
    operations = {record.get("operation") or first["relation"], first["relation"]}
    situation = unit_situation(kind, record, operations)
    evidence: list[dict] = []
    place = record.get("provision") or ""

    def add(passages):
        for passage in passages:
            evidence.append({**passage, "change_id": record.get("change_id"), "place": place})
        return passages

    sent = {"changed_act": {"celex": first["changed"]["celex"], "title": first["changed"]["title"]},
            "changing_act": {"celex": first["changing"]["celex"], "title": first["changing"]["title"],
                             "date": first["changing"].get("date")},
            "situation": situation, "situation_rule": SITUATIONS[situation]}
    subject = None
    if kind == "CORRIGENDUM":
        corrections = []
        for number, correction in enumerate(first.get("corrections") or [], 1):
            if number > 12:
                break
            entry = {"location": correction["location"]}
            if correction["wrong"]:
                entry["wrong"] = add([{"id": f"W{number}", "kind": "WRONG", "text": correction["wrong"][:1500],
                                       "changed": True}])[0]["id"]
            entry["correct"] = add([{"id": f"R{number}", "kind": "CORRECT", "text": correction["correct"][:1500],
                                     "changed": True}])[0]["id"]
            corrections.append(entry)
        if corrections:
            sent["corrections"] = corrections
        else:
            add(evidence_passages(first.get("instruction"), "T"))
    else:
        old, new = record.get("old_text"), record.get("new_text")
        flags_old, flags_new = changed_flags(old, new) if old and new else (None, None)
        add(evidence_passages(old, "O", flags_old, share_of(old, new)))
        add(evidence_passages(new, "N", flags_new, share_of(new, old)))
        add(evidence_passages(record.get("instruction"), "I"))
        letter = point_letter(place)
        context = record.get("point_context") or ""
        subject = (point_subject(context, letter) or point_subject(new, letter) or point_subject(old, letter)
                   if letter else None)
        sent["place"] = {"place": place or ("the whole act" if kind == "ACT" else "several places"),
                         "operation": record.get("operation") or first["relation"]}
        if record.get("heading"):
            sent["place"]["heading"] = record["heading"]
        if letter:
            sent["place"]["point"] = f"({letter})"
            sent["place"]["subject_of_point"] = subject or ("not given: do not name what point "
                                                             f"({letter}) is about, write 'Punkt ({letter})'")
        if context:
            sent["place"]["current_wording_of_place"] = add(
                [{"id": "C1", "kind": "PLACE_CONTEXT", "text": squash(context)[:1500], "changed": None}])[0]["id"]
    finals = " ".join(f"{f['locator']}: {f['text']}" for f in first.get("final_articles") or [])
    add(evidence_passages(finals, "F", budget=3000))
    relevance, relevance_passages = relevance_part(routing, kind)
    if relevance:
        evidence.append({"id": "K1", "kind": "RELEVANCE", "text": relevance_passages["K1"], "changed": None,
                         "change_id": None, "place": place})
    by_kind = defaultdict(list)
    for passage in evidence:
        by_kind[passage["id"][0]].append(passage)
    sent["evidence"] = [{"id": p["id"], "type": p["kind"], "text": p["text"],
                         **({"changed": p["changed"]} if p["changed"] is not None and p["id"][0] in "ON" else {})}
                        for p in evidence if p["id"][0] != "K"]          # K1 is sent once, as why_relevant
    # What each field may cite.
    pair = bool(WORD_PAIR.search(record.get("instruction") or ""))
    if kind == "CORRIGENDUM":
        before_ids = [p["id"] for p in by_kind["W"] + by_kind["T"]]
        after_ids = [p["id"] for p in by_kind["R"] + by_kind["T"]]
    else:
        old_changed = [p["id"] for p in by_kind["O"] if p["changed"] is not False]
        new_changed = [p["id"] for p in by_kind["N"] if p["changed"] is not False]
        before_ids = old_changed + ([p["id"] for p in by_kind["I"]] if pair or situation.startswith("REMOVAL") else [])
        after_ids = [] if situation.startswith("REMOVAL") else new_changed + [p["id"] for p in by_kind["I"]]
    date_ids = [p["id"] for p in evidence if p["id"][0] in "FIN"]
    dates = auto_dates(record if kind == "PROVISION" else {}, first)
    if dates:
        sent["dates_known"] = [{"what": d["source"], "date": d["date"]} for d in dates]
    notes = []
    if situation in {"NEW_ONLY", "INSTRUCTION"} and not pair:
        notes.append("No old wording: describe what applies now. Never write früher, bisher, nicht mehr, erweitert, "
                     "verlängert, gesenkt, erhöht, konkretisiert or the like; write 'gilt nun bis …', 'umfasst nun …'.")
    if any(p["changed"] for p in by_kind["O"] + by_kind["N"]):
        notes.append("Passages marked changed=false did not change: never use them as evidence of a difference.")
    if notes:
        sent["notes"] = notes
    if relevance:
        sent["why_relevant"] = relevance
    return {"kind": kind, "situation": situation, "record": record, "item_id": item["id"], "place": place,
            "evidence": {p["id"]: p for p in evidence}, "before_ids": before_ids, "after_ids": after_ids,
            "date_ids": date_ids, "subject": subject, "letter": point_letter(place), "dates": dates,
            "operations": operations, "pair": pair, "trimmed": bool(record.get("trimmed")), "sent": sent,
            "relevance": bool(relevance)}


def share_of(text, other) -> int:
    return TEXT_BUDGET if not other else max(2000, TEXT_BUDGET * len(text or "") // max(1, len(text or "") +
                                                                                         len(other or "")))


def auto_dates(record: dict, first: dict) -> list[dict]:
    """Dates Python knows without Qwen, each with its source: the change's own date, then the amending act's
    entry into force and adoption. Never mixed: application of a change is not entry into force of the act."""
    dates = []
    if record.get("effective_from"):
        dates.append({"source": record.get("effective_source") or "CHANGE", "date": str(record["effective_from"])[:10]})
    changing = first["changing"]
    if changing.get("entry_into_force"):
        dates.append({"source": "ENTRY_INTO_FORCE", "date": str(changing["entry_into_force"])[:10]})
    if changing.get("date"):
        dates.append({"source": "ADOPTION", "date": str(changing["date"])[:10]})
    return dates


def unit_schema(unit: dict) -> dict:
    enum = lambda ids: {"type": "string", "enum": ids or ["NONE"]}  # noqa: E731
    facts_allowed = bool(unit["before_ids"] or unit["after_ids"])
    fact = {"type": "object", "additionalProperties": False,
            "required": ["what", "before", "after", "before_evidence", "after_evidence"],
            "properties": {"what": {"type": "string", "maxLength": LIMITS["what"]},
                           "before": {"type": "string", "maxLength": LIMITS["value"] if unit["before_ids"] else 0},
                           "after": {"type": "string", "maxLength": LIMITS["value"] if unit["after_ids"] else 0},
                           "before_evidence": {"type": "array", "maxItems": FACT_EVIDENCE if unit["before_ids"] else 0,
                                               "items": enum(unit["before_ids"])},
                           "after_evidence": {"type": "array", "maxItems": FACT_EVIDENCE if unit["after_ids"] else 0,
                                              "items": enum(unit["after_ids"])}}}
    relevance_ids = ["K1"] if unit["relevance"] else []
    properties = {
        "headline": {"type": "string", "maxLength": LIMITS["headline"]},
        "facts": {"type": "array", "maxItems": MAX_FACTS if facts_allowed else 0, "items": fact},
        "importance": importance_schema(relevance_ids),
        "to_confirm": importance_schema(relevance_ids, "to_confirm"),
    }
    if unit["kind"] == "CORRIGENDUM":
        properties["what_was_wrong"] = {"type": "string", "maxLength": LIMITS["text"]}
        properties["affects"] = {"type": "string", "enum": list(AFFECTS)}
    else:
        properties["what_changed"] = {"type": "string", "maxLength": LIMITS["text"]}
        properties["significance"] = {"type": "string", "enum": list(SIGNIFICANCE)}
        properties["dates"] = {"type": "array", "maxItems": 3 if unit["date_ids"] else 0, "items": {
            "type": "object", "additionalProperties": False, "required": ["kind", "date", "evidence"],
            "properties": {"kind": {"type": "string", "enum": list(DATE_KINDS)},
                           "date": {"type": "string", "maxLength": LIMITS["date"]},
                           "evidence": {"type": "array", "minItems": 1, "maxItems": 2, "items": enum(unit["date_ids"])}}}}
    return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}


def quote(unit: dict, ids: list[str]) -> list[dict]:
    """The quotations, copied by Python from the selected passages: never written by Qwen."""
    return [{"id": i, "ref": f"{unit['evidence'][i]['change_id'] or unit['item_id']}:{i}",
             "type": unit["evidence"][i]["kind"], "place": unit["evidence"][i]["place"],
             "text": unit["evidence"][i]["text"]} for i in ids]


def evidence_text(unit, kinds) -> str:
    return " ".join(p["text"] for p in unit["evidence"].values() if p["id"][0] in kinds)


def check_values(value: str, cited: str, what: str):
    """Numbers and dates of a value must stand in the cited evidence; place references do not count as values."""
    problems = number_problems([without_places(value)], cited)
    if problems:
        raise Unclassified(f"In {what}: {problems[0]}")


def validate_unit(reply, unit: dict, language: str = "de") -> dict:
    schema_keys = set(unit_schema(unit)["properties"])
    if not isinstance(reply, dict) or set(reply) != schema_keys:
        raise Unclassified(f"The reply must hold exactly {', '.join(sorted(schema_keys))}.")
    corrigendum = unit["kind"] == "CORRIGENDUM"
    headline = text_field(reply, "headline", LIMITS["headline"])
    text = text_field(reply, "what_was_wrong" if corrigendum else "what_changed", LIMITS["text"])
    every = " ".join(p["text"] for p in unit["evidence"].values()) + " " + " ".join(
        d["date"] for d in unit["dates"])
    check_public_text((headline, text), unit["operations"])
    check([without_places(headline), without_places(text)], every, "headline or text")
    facts = []
    for number, fact in enumerate(reply.get("facts") or [], 1):
        if not isinstance(fact, dict):
            raise Unclassified(f"Fact {number} must be an object.")
        what = squash(fact.get("what")) if isinstance(fact.get("what"), str) else ""
        before, after = squash(fact.get("before") or ""), squash(fact.get("after") or "")
        before_ids = list(dict.fromkeys(fact.get("before_evidence") or []))
        after_ids = list(dict.fromkeys(fact.get("after_evidence") or []))
        if not what:
            raise Unclassified(f"Fact {number} needs what.")
        if any(i not in unit["before_ids"] for i in before_ids) or any(i not in unit["after_ids"] for i in after_ids):
            raise Unclassified(f"Fact {number} cites a passage that cannot support it.")
        if before and not before_ids:
            raise Unclassified(f"Fact {number}: before needs before_evidence (an old passage).")
        if after and not after_ids:
            raise Unclassified(f"Fact {number}: after needs after_evidence (a new passage or the instruction).")
        if not before and not after:
            documented = unit["situation"].startswith("REMOVAL") and any(i[0] in "IO" for i in before_ids)
            if not documented:
                raise Unclassified(f"Fact {number} needs before or after, each with its evidence.")
        if before and after and signature(before) == signature(after):
            raise Unclassified(f"Fact {number}: before and after are the same; name only real differences.")
        if before:
            check_values(before, " ".join(unit["evidence"][i]["text"] for i in before_ids), f"before of fact {number}")
        if after:
            check_values(after, " ".join(unit["evidence"][i]["text"] for i in after_ids), f"after of fact {number}")
        facts.append({"what": what, "before": before, "after": after, "passages": before_ids + after_ids,
                      "before_evidence": quote(unit, before_ids), "after_evidence": quote(unit, after_ids),
                      "change_id": unit["record"].get("change_id"), "place": unit["place"],
                      "quotes": [unit["evidence"][i]["text"][:400] for i in before_ids + after_ids]})
    public = [headline, text] + [f"{f['what']} {f['before']} {f['after']}" for f in facts]
    check_subject([headline] + [f"{f['what']} {f['before']} {f['after']}" for f in facts], [text], unit)
    if corrigendum:
        if reply["affects"] not in AFFECTS:
            raise Unclassified(f"affects is one of {', '.join(AFFECTS)}.")
        if not facts and unit["after_ids"]:
            raise Unclassified("Name at least one correction in facts, with the correct wording as after_evidence.")
        if reply["affects"] == "WORDING_ONLY" and any(
                number_problems([f["after"]], f["before"]) for f in facts if f["before"] and f["after"]):
            raise Unclassified("A corrected number or date is never WORDING_ONLY. Use NUMBER or DATE.")
    else:
        significance = reply["significance"]
        if significance not in SIGNIFICANCE:
            raise Unclassified(f"significance is one of {', '.join(SIGNIFICANCE)}.")
        comparable = unit["situation"] == "BOTH" or unit["pair"]
        if not comparable and not unit["situation"].startswith("REMOVAL") and \
                not (unit["operations"] & ADDITIONS) and (word := COMPARING.search(" ".join(public))):
            raise Unclassified(f"'{word.group(0)}' compares with an old wording that is not given. Write what applies "
                               "now, for example 'gilt nun bis …' or 'umfasst nun …'.")
        if unit["situation"].startswith("REMOVAL") and (word := NEW_CLAIM.search(f"{headline} {text}")):
            raise Unclassified(f"'{word.group(0)}': the place is removed, nothing is new. Say what no longer applies.")
        if significance == "EDITORIAL":
            if unit["trimmed"] or unit["situation"] != "BOTH":
                raise Unclassified("EDITORIAL needs the complete old and new wording. Use SUBSTANTIVE or UNCLEAR.")
            if any(number_problems([f["after"]], f["before"]) for f in facts if f["before"] and f["after"]):
                raise Unclassified("A changed number or date is never EDITORIAL. Use SUBSTANTIVE.")
            if REPLACEMENT_PLACEHOLDER.search(evidence_text(unit, "NI")):
                raise Unclassified("Replacing a provision by 'Not used' removes its content: not EDITORIAL.")
        if significance == "SUBSTANTIVE" and not facts and (unit["before_ids"] or unit["after_ids"]):
            raise Unclassified("A substantive change needs at least one fact with its evidence.")
    dates = [{"what": AUTO_DATES[language].get(d["source"], d["source"]), "date": d["date"], "source": d["source"],
              "passages": [], "evidence": [], "auto": True} for d in unit["dates"]]
    for number, date in enumerate(reply.get("dates") or [], 1):
        value, ids = squash(date.get("date") or ""), list(dict.fromkeys(date.get("evidence") or []))
        if date.get("kind") not in DATE_KINDS or not re.search(r"\d", value) or not ids or \
                any(i not in unit["date_ids"] for i in ids):
            raise Unclassified(f"Date {number} needs a kind, a date and evidence from the instruction or final articles.")
        cited = " ".join(unit["evidence"][i]["text"] for i in ids)
        check([value], cited, f"date {number}")
        if not re.search(DATE_SUPPORT[date["kind"]], cited, re.I):
            raise Unclassified(f"Date {number}: the cited passage does not say it is a {date['kind']} date. "
                               "Leave optional dates out when their purpose is unclear.")
        labels = DATE_KINDS if language == "de" else DATE_KINDS_EN
        dates.append({"what": labels[date["kind"]], "date": iso_date(value) or value, "as_written": value, "kind": date["kind"], "passages": ids,
                      "evidence": quote(unit, ids), "source": "EVIDENCE"})
    importance, to_confirm = check_importance(reply, {"K1": 1} if unit["relevance"] else {}, every)
    check_public_text((importance, to_confirm))
    check_language([headline, text, importance] + [f["what"] for f in facts])
    answer = {"headline": headline, "facts": facts, "importance": importance, "to_confirm": to_confirm,
              "situation": unit["situation"], "change_id": unit["record"].get("change_id"), "place": unit["place"],
              "subject_of_point": unit["subject"]}
    if corrigendum:
        answer.update(what_was_wrong=text, affects=reply["affects"])
    else:
        significance = reply["significance"]
        if unit["situation"].startswith("REMOVAL") and significance == "EDITORIAL":
            significance = "UNCLEAR"
        answer.update(what_changed=text, significance=significance, dates=dates)
    return answer


MONTHS = {name: number for number, names in enumerate(
    (("january", "januar", "jan"), ("february", "februar", "feb"), ("march", "märz", "maerz", "mar"),
     ("april", "apr"), ("may", "mai"), ("june", "juni", "jun"), ("july", "juli", "jul"), ("august", "aug"),
     ("september", "sept", "sep"), ("october", "oktober", "oct", "okt"), ("november", "nov"),
     ("december", "dezember", "dec", "dez")), 1) for name in names}


def iso_date(value: str) -> str | None:
    """2026-07-01 for "1 July 2026", "1. Juli 2026", "1.7.2026" or "2026-07-01"; None when it is no single date."""
    text = squash(value).casefold()
    for pattern, order in ((r"^(\d{4})-(\d{1,2})-(\d{1,2})$", "ymd"), (r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", "dmy"),
                           (r"^(\d{1,2})\.?\s+([a-zäö]+)\.?\s+(\d{4})$", "dMy")):
        match = re.match(pattern, text)
        if not match:
            continue
        a, b, c = match.groups()
        if order == "ymd":
            year, month, day = int(a), int(b), int(c)
        elif order == "dmy":
            day, month, year = int(a), int(b), int(c)
        else:
            day, month, year = int(a), MONTHS.get(b), int(c)
        if month and 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def check_subject(strict: list[str], loose: list[str], unit: dict):
    """A procurement value stays attached to the category its point names: "(b) service contracts …" may not be
    called a works contract, and without the point's own wording no category may be named at all.
    strict (headline, facts): only the point's own subject. loose (the sentences): also categories the point's
    wording mentions ("service contracts … connected to a works contract")."""
    if unit["letter"]:
        allowed_strict = categories_in(unit["subject"] or "")
        own = point_subject_clause(unit)
        allowed_loose = allowed_strict | (categories_in(own) if unit["subject"] else set())
        where = f"point ({unit['letter']})"
    else:
        allowed_strict = allowed_loose = categories_in(evidence_text(unit, "ONIC"))
        where = "this place"
    wrong = (categories_in(" ".join(strict)) - allowed_strict) | (categories_in(" ".join(loose)) - allowed_loose)
    if wrong:
        about = f" Point ({unit['letter']}) is about: {unit['subject']}." if unit["subject"] else ""
        raise Unclassified(f"The evidence for {where} does not name {', '.join(sorted(wrong))} contracts.{about} "
                           f"Use the words of the point, or write 'Punkt ({unit['letter'] or '…'})'.")


def ask_unit(client, system: str, unit: dict, task: str, name: str, language: str, max_tokens: int):
    """One request with at most one correction. Returns (answer, trace); raises PartFailed(trace) when the second
    answer is still invalid. The trace holds the evidence sent, every raw answer and every validation error."""
    schema = unit_schema(unit)
    evidence = json.dumps(unit["sent"], ensure_ascii=False)
    trace = {"sent": unit["sent"], "attempts": [], "situation": unit["situation"], "place": unit["place"],
             "change_id": unit["record"].get("change_id")}
    messages = [{"role": "system", "content": system}, {"role": "user", "content": f"{evidence}\n\n{task}"}]
    for attempt in range(2):
        try:
            reply = client.chat(messages, thinking=False, stream=False, seed=42, sampling={"temperature": 0},
                                schema=schema, schema_name=name, max_tokens=max_tokens)
        except Exception as exc:  # noqa: BLE001 - the request could not be sent at all
            raise ConnectionLost(str(exc)) from exc
        usage = reply.usage or {}
        step = {"seconds": round(getattr(reply, "seconds", 0.0) or 0.0, 3),
                "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
                "finish_reason": getattr(reply, "finish_reason", None)}
        if not reply.ok:
            if reply.error_kind == "context_limit":
                trace["attempts"].append({**step, "error": "context limit"})
                raise PartFailed("The evidence of this change is too long for the model.", trace)
            raise ConnectionLost(client.clean(reply.error or f"Model request failed ({reply.error_kind})."))
        candidate, error = reply.parse_json()
        step["answer"] = candidate if candidate is not None else (reply.content or "")[:4000]
        try:
            if error:
                raise Unclassified(error)
            answer = validate_unit(candidate, unit, language)
            trace["attempts"].append({**step, "error": None})
            return answer, trace
        except Unclassified as exc:
            step["error"] = str(exc)
            trace["attempts"].append(step)
            if attempt:
                raise PartFailed(f"Invalid answer after one correction request: {exc}", trace) from exc
            messages[1]["content"] = (f"{evidence}\n\n{task}\n\nCORRECTION: your previous reply was rejected. "
                                      f"{exc} Return the complete JSON object again.")


def failure_kind(message: str) -> str:
    """A short category for a failed part, for summary.json."""
    for kind, pattern in (("CONTEXT_LIMIT", r"too long"), ("ANSWER_CUT_OFF", r"cut off|finish_reason=length"),
                          ("SUBJECT", r"does not name .* contracts"), ("COMPARISON_WITHOUT_OLD", r"compares with"),
                          ("NEW_CLAIM_IN_REMOVAL", r"nothing is new"), ("DATE", r"\bdate\b|Date \d"),
                          ("UNSUPPORTED_NUMBER", r"does not occur|number"), ("EVIDENCE", r"evidence|cites a passage"),
                          ("INCOMPLETE_FACT", r"Fact \d+ needs|needs at least one fact"),
                          ("EDITORIAL", r"EDITORIAL"), ("LANGUAGE", r"Write everything in"),
                          ("FORMAT", r"must hold exactly|JSON")):
        if re.search(pattern, message, re.I):
            return kind
    return "OTHER"


def explain(client, systems, item, log, routing=None, part_dir=None):
    """Explain every single change of an item as its own unit. Successful parts are kept (checkpoints in
    part_dir); a later start asks again only the failed parts. Outcome: EXPLAINED, PARTIAL or (raised) FAILED."""
    routing = routing or {}
    facts = item_facts(item)
    flags = review_reasons({"facts": facts, "routing": routing, "kind": item["kind"],
                            "explanation": {"significance": "SUBSTANTIVE"}})
    context = routing if "PROVISION_RELEVANCE_UNVERIFIED" not in flags else None
    units = item["records"] if item["kind"] == "PROVISION" else item["records"][:1]
    parts, answers = [], []
    for position, record in enumerate(units, 1):
        single = {**item, "records": [one_change(record)] if item["kind"] == "PROVISION" else [record]}
        base = {"position": position, "change_id": record.get("change_id"), "place": record.get("provision") or ""}
        reason = missing_reason(single)
        if reason:
            parts.append({**base, "status": "MISSING_EVIDENCE", "reason": reason})
            continue
        checkpoint = part_dir / f"{position}.json" if part_dir is not None else None
        saved = read_json(checkpoint) if checkpoint is not None and checkpoint.is_file() else {}
        if saved.get("status") == "OK":
            answers.append((saved["prompt"], record, saved["answer"]))
            parts.append({**base, "status": "OK", "situation": saved["answer"].get("situation")})
            continue
        name = "corrigendum" if item["kind"] == "CORRIGENDUM" else "change"
        unit = build_unit(item, record, context)
        tokens = min(4000, 1600 + 120 * len(unit["evidence"]))
        try:
            answer, trace = ask_unit(client, systems[name], unit, TASKS[name], f"{name}_explanation", LANGUAGE, tokens)
        except PartFailed as exc:
            log.extend({k: v for k, v in step.items() if k != "answer"} for step in exc.trace["attempts"])
            parts.append({**base, "status": "FAILED", "error": str(exc), "kind": failure_kind(str(exc)),
                          "situation": unit["situation"]})
            if checkpoint is not None:
                write_json(checkpoint, {"status": "FAILED", "error": str(exc), "kind": failure_kind(str(exc)),
                                        **exc.trace})
            continue
        log.extend({k: v for k, v in step.items() if k != "answer"} for step in trace["attempts"])
        if checkpoint is not None:
            write_json(checkpoint, {"status": "OK", "prompt": name, "answer": answer, **trace})
        answers.append((name, record, answer))
        parts.append({**base, "status": "OK", "situation": unit["situation"]})
    failed = [p for p in parts if p["status"] == "FAILED"]
    if not answers:
        if failed:
            raise Unclassified(" | ".join(f"{p['place'] or p['change_id']}: {p['error']}" for p in failed[:4]))
        raise Unclassified("The individual changes have no usable evidence.")
    return combine(answers, parts)


def combine(answers, parts) -> tuple[str, dict]:
    """One explanation for the item from its explained parts; the missing parts stay listed."""
    name, _, first = answers[0]
    missing = [p["place"] or p["change_id"] for p in parts if p["status"] != "OK"]
    outcome = "PARTIAL" if missing else "EXPLAINED"
    if len(answers) == 1:
        return name, {**first, "outcome": outcome, "parts": parts, "missing_changes": missing}
    statuses = {a.get("significance") for _, _, a in answers}
    significance = ("UNCLEAR" if missing or "UNCLEAR" in statuses
                    else "SUBSTANTIVE" if "SUBSTANTIVE" in statuses else "EDITORIAL")
    text = " ".join(f"{a.get('place') or ''}: {a.get('what_changed') or a.get('what_was_wrong')}".strip(": ")
                    for _, _, a in answers)
    dates, seen = [], set()
    for _, _, answer in answers:
        for date in answer.get("dates", []):
            key = (date["what"], date["date"], date.get("auto") and answer.get("place"))
            if key not in seen:
                seen.add(key)
                dates.append({**date, "place": answer.get("place")})
    return name, {**first, "what_changed": text, "significance": significance, "outcome": outcome,
                  "importance": " ".join(dict.fromkeys(a["importance"] for _, _, a in answers if a["importance"])),
                  "to_confirm": " ".join(dict.fromkeys(a["to_confirm"] for _, _, a in answers if a["to_confirm"])),
                  "facts": [f for _, _, a in answers for f in a["facts"]], "dates": dates,
                  "parts": parts, "missing_changes": missing}


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
    explanation = record.get("explanation") or {}
    significance = explanation.get("significance") or explanation.get("affects")
    return bool(significance and significance not in {"EDITORIAL", "WORDING_ONLY"} and not review_reasons(record))


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
        lines.append({**change, "operation_label": OPERATION_LABEL[language].get(operation, operation),
                      "old_text": (text.get("old") or "")[:4000], "new_text": (text.get("new") or "")[:4000],
                      "wording_notes": notes})
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
    e = html.escape
    out = [f'<div class="card"><h2>{e(card["title"] or card["celex"])}</h2>'
           f'<div class="meta">{e(card["celex"])}{" · " + e(", ".join(card["role"]["labels"])) if card.get("role") else ""}'
           f' · <a href="{e(card["eurlex_url"])}">EUR-Lex</a></div>']
    for change in card["changes"]:
        out.append(f'<div class="change"><span class="tag {e(change["label"])}">{e(change["label"])}</span>'
                   f'<b>{e(change["provision"] or "")} {e(change["headline"])}</b>'
                   f'<div class="meta">{e(change["changing"]["celex"])} {e(change["changing"]["title"][:120])}'
                   f'{" · ab " + e(change["effective_from"]) if change["effective_from"] else ""}</div>'
                   f'<p>{e(change["text"])}</p>')
        if change.get("importance"):
            out.append(f'<p><b>{"Warum wichtig für Max Bögl" if language == "de" else "Why it matters for Max Bögl"}:'
                       f'</b> {e(change["importance"])}'
                       + (f' <i>{e(change["to_confirm"])}</i>' if change.get("to_confirm") else "") + "</p>")
        for line in change.get("changes") or []:
            out.append(f'<div class="fact">{e(line.get("provision") or "")} · {e(line.get("operation_label") or "")}'
                       f'{" · " + e(line["effective_from"]) + " (" + e(line["effective_source"]) + ")" if line.get("effective_from") else ""}'
                       + "".join(f' · <span class="note">{e(n)}</span>' for n in line.get("wording_notes") or [])
                       + "</div>")
        for fact in change["facts"]:
            out.append(f'<div class="fact">{e(fact.get("place") or "")} {e(fact["what"])}: '
                       f'<span class="before">{e(fact["before"])}</span> → <span class="after">{e(fact["after"])}</span>'
                       + "".join(f'<div class="note">{e(q["type"])} {e(q["ref"])}: „{e(q["text"][:300])}“</div>'
                                 for q in fact.get("before_evidence", []) + fact.get("after_evidence", []))
                       + "</div>")
        for day in change["dates"]:
            source = f' ({e(day["source"])})' if day.get("source") else ""
            out.append(f'<div class="fact">{e(day["what"])}: <b>{e(day["date"])}</b>{source}</div>')
        if change.get("missing_parts"):
            out.append(f'<p class="note">{"Nicht erklärt" if language == "de" else "Not explained"}: '
                       f'{e(", ".join(change["missing_parts"]))}</p>')
        if change.get("review_reasons"):
            out.append(f'<p class="note">{"Prüfung" if language == "de" else "Review"}: '
                       f'{e(", ".join(change["review_reasons"]))}</p>')
        if any(line["old_text"] or line["new_text"] for line in change.get("changes") or []):
            out.append(f'<details><summary>{"Wortlaut vorher / nachher" if language == "de" else "Old / new wording"}'
                       '</summary>' + "".join(f'<p class="meta">{e(line.get("provision") or "")}</p>'
                                              f'<pre>{e(line["old_text"])}</pre><pre>{e(line["new_text"])}</pre>'
                                              for line in change["changes"] if line["old_text"] or line["new_text"])
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
    return {"outcomes": {k: items[k] for k in ("EXPLAINED", "PARTIAL", "FAILED", "MISSING_EVIDENCE", "SCOPE_REVIEW",
                                                "OPEN")},
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
    BUSINESS_AREAS.update(load_business_areas(ROOT / "config" / f"business_anchors_{args.language}.yaml"))
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
    fingerprint = sha([systems, TASKS, SITUATIONS, DATE_KINDS, sorted(CATEGORY_TERMS), LIMITS, ACT_SCHEMA,
                       args.language, CORE_VERSION, planned, results, args.link_template])
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
