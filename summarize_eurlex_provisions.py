"""Write the summaries for the department inbox.

Input: the classified provisions (classify_eurlex_provisions.py) and the saved
text packs of the relevance runs. Three steps, all with Qwen:

1. Provision summary  - what one article, annex or table says (a small paragraph
                        and up to 4 key points).
                        Written from the legal text alone. Every bullet names
                        the passages it is taken from.
2. Act summary        - what the act is about (a small paragraph of 3-5 sentences),
                        from the title, the act's subject and the provision summaries.
3. Department summary - why the act matters to Max Bögl and why it came to this
                        department (1-4 bullets), from the saved relevance and
                        classification reasons and the provision summaries.

The code checks every answer: named passages and provisions must exist, and
every number in a summary must occur in the text it was written from.

Output folder (default runs/eurlex_summaries_v1):
    inbox/<department>.json   the cards of one department, ready for the UI
    preview/index.html        the same cards as simple pages to look at
    review.csv                one row per provision, to check by hand
    summary.json              counts

Nothing in other run folders is changed. Ctrl+C stops; the same command
continues. When Qwen cannot be reached it waits 15, 30 and 45 minutes, then
every 60 minutes.

    python summarize_eurlex_provisions.py --count-only
    python summarize_eurlex_provisions.py --sample 50 --levels DIRECT
    python summarize_eurlex_provisions.py --levels DIRECT
"""

import argparse
from collections import Counter, defaultdict
import csv
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
from eurlex_summary_checks import number_problems, states_nothing, wrong_language  # noqa: E402
from lr_ai.qwen_client import QwenClient  # noqa: E402

PROMPTS = {name: ROOT / f"prompts/eurlex_{name}_summary.md"
           for name in ("provision", "merge", "table", "act", "department")}
PROVISION_PROMPTS = ("provision", "merge")   # only these decide whether a folder can be continued
DEFAULT_CLASSIFICATION = ROOT / "runs/eurlex_classification_v5"
DEFAULT_OUTPUT = ROOT / "runs/eurlex_summaries_v1"
TASKS = {
    "provision": "TASK: Summarise this provision. Return the JSON object described in the instructions.",
    "merge": "TASK: Write one headline and one overview for the whole provision. Return the JSON object described "
             "in the instructions.",
    "table": "TASK: Write one summary for this group of tables. Return the JSON object described in the "
             "instructions.",
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
MAX_ACT_HEADLINES = 12        # provisions shown to Qwen for one act summary, spread over the whole act
MAX_ACT_DIRECT = 6            # of them at most this many DIRECT ones first, the rest spread over the act
MAX_SENTENCES = 5
TABLE_GROUP_MIN = 3           # tables of one annex section summarised together from this many on
MAX_TABLES_SHOWN = 15         # tables of a group shown to Qwen, spread over the group
# A new version makes the run write that step again in the same folder; the provision summaries are kept.
ACT_VERSION = "act-v2"
TABLE_VERSION = "tables-v1"
CUT_REDO = "cut-v1"           # provisions whose text was stopped at the old length limit are asked once more
MAX_CARD_PROVISIONS = 15      # provisions shown to Qwen for one department summary
# The old length limits (v4.5 and before). Only used to find the saved texts that were stopped at them; nothing that
# Qwen writes now is limited, cut or rejected because of its length.
LIMITS = {"headline": 140, "overview": 700, "bullet": 380, "sentence": 450, "department_bullet": 450}


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
    """The shape of the answer. No length limits: Qwen writes every text to its end."""
    return {
        "type": "object", "additionalProperties": False, "required": ["headline", "overview", "bullets"],
        "properties": {
            "headline": {"type": "string"},
            "overview": {"type": "string"},
            "bullets": {"type": "array", "minItems": 1, "maxItems": MAX_BULLETS, "items": {
                "type": "object", "additionalProperties": False, "required": ["kind", "text", "passages"],
                "properties": {
                    "kind": {"type": "string", "enum": list(BULLET_KINDS)},
                    "text": {"type": "string"},
                    "passages": {"type": "array", "minItems": 1, "maxItems": 3,
                                 "items": {"type": "string", "enum": list(passage_ids)}},
                }}},
        },
    }


def number_marks(texts, source):
    """The numbers, units or dates of a text that were not found in the source. Only a mark for the reader of
    review.csv; nothing is rejected or changed."""
    return number_problems([text for text in texts if text], source)


def validate_provision(reply, passages, source, locator="", final=False):
    """Qwen's answer is taken as it is. Only an answer that cannot be read (not the three fields) is asked once
    more. Nothing is cut, shortened or left out; a number not found in the text is only marked (`number_check`)."""
    if not isinstance(reply, dict) or not {"headline", "overview", "bullets"} <= set(reply):
        raise Unclassified("The reply must hold headline, overview and bullets.")
    headline = squash(reply["headline"]) if isinstance(reply["headline"], str) else ""
    overview = squash(reply["overview"]) if isinstance(reply["overview"], str) else ""
    result = []
    for bullet in reply["bullets"] if isinstance(reply["bullets"], list) else []:
        if not isinstance(bullet, dict) or not isinstance(bullet.get("text"), str) or not squash(bullet["text"]):
            continue
        cited = [mark for mark in bullet.get("passages") or [] if mark in passages]
        result.append({"kind": bullet.get("kind") if bullet.get("kind") in BULLET_KINDS else "OTHER",
                       "text": squash(bullet["text"]), "passages": list(dict.fromkeys(cited))[:3]})
    for bullet in result:
        if bullet["passages"]:
            cite_numbers(bullet, passages, locator)
        marks = number_marks([bullet["text"]], source)
        if marks:
            bullet["number_check"] = marks
    answer = {"headline": headline, "overview": overview, "bullets": result}
    marks = number_marks([headline, overview], source)
    if marks:
        answer["number_check"] = marks
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
    "properties": {"headline": {"type": "string"}, "overview": {"type": "string"}},
}


def validate_merge(reply, source):
    if not isinstance(reply, dict) or not {"headline", "overview"} <= set(reply):
        raise Unclassified("The reply must hold headline and overview.")
    headline, overview = (squash(reply[key]) if isinstance(reply[key], str) else "" for key in ("headline", "overview"))
    if not headline and not overview:
        raise Unclassified("The reply holds no text.")
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
                         log, 2000)
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
                   lambda reply: validate_merge(reply, source), log, 1200)
    except (Unclassified, OverflowError):
        return None


# ------------------------------------------------------------------ act type, order of provisions

TYPE_WORDS = {"de": ("Verordnung", "Richtlinie", "Beschluss", "Entscheidung", "Empfehlung"),
              "en": ("Regulation", "Directive", "Decision", "Recommendation")}
CELEX_PARTS = re.compile(r"\d(\d{4})([A-Z]{1,2})(\d{4})(?:\(\d+\))?")


def act_type(celex, language):
    """The type of act from the CELEX number: 32019R1020 -> Verordnung, 32015D0975 -> Beschluss. None if unknown."""
    match = CELEX_PARTS.fullmatch(celex or "")
    if not match:
        return None
    year, letter = int(match.group(1)), match.group(2)
    if language == "de":
        # Decisions adopted before the Lisbon Treaty (December 2009) are "Entscheidung" in German.
        names = {"R": "Verordnung", "L": "Richtlinie", "D": "Entscheidung" if year <= 2009 else "Beschluss",
                 "H": "Empfehlung"}
    else:
        names = {"R": "Regulation", "L": "Directive", "D": "Decision", "H": "Recommendation"}
    return names.get(letter)


def act_numbers(celex):
    """How the act number can be written: 2015/975, 15/975, 975/2015, 975/15."""
    match = CELEX_PARTS.fullmatch(celex or "")
    if not match:
        return []
    year, number = match.group(1), str(int(match.group(3)))
    return [f"{year}/{number}", f"{year[2:]}/{number}", f"{number}/{year}", f"{number}/{year[2:]}"]


def wrong_type_word(sentences, celex, language):
    """The type word used for this act when it is the wrong one ("Verordnung 2015/975" for a decision)."""
    expected, numbers = act_type(celex, language), act_numbers(celex)
    if not expected or not numbers:
        return None
    words = "|".join(TYPE_WORDS[language])
    pattern = re.compile(rf"\b({words})\b(?:(?!{words}).){{0,30}}?(?<![\d/])(?:{'|'.join(map(re.escape, numbers))})"
                         r"(?!\d)(?!/\d)")
    accepted = {"Beschluss", "Entscheidung"} if expected in ("Beschluss", "Entscheidung") else {expected}
    for sentence in sentences:
        for match in pattern.finditer(sentence):
            if match.group(1) not in accepted:
                return match.group(1)
    return None


ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def roman_value(text):
    total = 0
    for index, letter in enumerate(text):
        value = ROMAN.get(letter, 0)
        total += -value if index + 1 < len(text) and ROMAN.get(text[index + 1], 0) > value else value
    return total


def plain_locator(locator):
    return re.sub(r"\s*\[source path [^\]]*\]", "", locator or "").strip()


def locator_key(locator):
    """Order of the act: articles by number, then annexes by number, then the rest; tables by their number."""
    text = plain_locator(locator)
    match = re.match(r"(Article|Artikel|Annex|Anhang)\s*(\d+|[IVXLC]+\b)?", text, re.I)
    natural = [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", text)]
    if not match:
        return (2, 0, natural)
    number = match.group(2) or ""
    value = int(number) if number.isdigit() else roman_value(number.upper())
    return (0 if match.group(1).lower().startswith("art") else 1, value, natural)


def spread(items, limit):
    """At most `limit` items taken evenly from the whole list, in their order."""
    if len(items) <= limit:
        return list(items)
    if limit <= 1:
        return list(items[:limit])
    return [items[round(index * (len(items) - 1) / (limit - 1))] for index in range(limit)]  # first and last kept


# ------------------------------------------------------------------ step 2: table groups

# "Annex 1, B. ... [section 5], unnumbered table 10" and "Annex VII, Table 4" belong to "Annex 1, B. ... [section 5]"
# and "Annex VII". A table that has its own title ("Annex IV, Table 1 — Product areas [section 4], unnumbered
# table 1") stays alone.
TABLE_PIECE = re.compile(r"^(?P<base>.+?),\s*(?:unnumbered table\s+\d+|Table\s+\d+[a-z]?)\s*(?:\[[^\]]*\])?\s*$", re.I)


def table_base(target):
    match = TABLE_PIECE.match(plain_locator(target))
    return match.group("base") if match else None


def table_groups_of(celex, records):
    """{key: {"base": section, "records": [...]}} for the sections with at least TABLE_GROUP_MIN tables."""
    by_base = defaultdict(list)
    for record in records:
        base = table_base(record["target"])
        if base:
            by_base[base].append(record)
    return {sha([celex, base])[:12]: {"base": base, "records": sorted(found, key=lambda r: locator_key(r["target"]))}
            for base, found in sorted(by_base.items()) if len(found) >= TABLE_GROUP_MIN}


def table_schema():
    return {
        "type": "object", "additionalProperties": False, "required": ["headline", "overview", "bullets"],
        "properties": {
            "headline": {"type": "string"},
            "overview": {"type": "string"},
            "bullets": {"type": "array", "minItems": 1, "maxItems": MAX_BULLETS, "items": {
                "type": "object", "additionalProperties": False, "required": ["kind", "text"],
                "properties": {"kind": {"type": "string", "enum": list(BULLET_KINDS)}, "text": {"type": "string"}}}},
        },
    }


def table_input(celex, title, base, records):
    kind = act_type(celex, OUTPUT_LANGUAGE or "de")
    shown = spread(records, MAX_TABLES_SHOWN)
    return {"act": {"celex": celex, "title": title, **({"act_type": kind} if kind else {})}, "section": base,
            "tables_total": len(records),
            "tables": [{"locator": plain_locator(r["target"]), "headline": r["summary"]["headline"],
                        "overview": r["summary"].get("overview", ""),
                        "points": [bullet["text"] for bullet in r["summary"]["bullets"]]} for r in shown]}


def validate_table(reply, source, final=False):
    """Taken as it is, like a provision summary; a number not found in the input is only marked."""
    if not isinstance(reply, dict) or not {"headline", "overview", "bullets"} <= set(reply):
        raise Unclassified("The reply must hold headline, overview and bullets.")
    headline, overview = (squash(reply[key]) if isinstance(reply[key], str) else "" for key in ("headline", "overview"))
    bullets = [{"kind": b.get("kind") if b.get("kind") in BULLET_KINDS else "OTHER", "text": squash(b["text"])}
               for b in (reply["bullets"] if isinstance(reply["bullets"], list) else [])
               if isinstance(b, dict) and isinstance(b.get("text"), str) and squash(b["text"])]
    answer = {"headline": headline, "overview": overview, "bullets": bullets}
    marks = number_marks([headline, overview, *(b["text"] for b in bullets)], source)
    if marks:
        answer["number_check"] = marks
    return answer


def summarise_table_group(client, system, sent):
    source, log = json.dumps(sent, ensure_ascii=False), []
    try:
        summary = ask(client, system, sent, TASKS["table"], table_schema(), "table_summary",
                      lambda reply, final=False: validate_table(reply, source, final), log, 1600)
    except OverflowError:
        if len(sent["tables"]) <= 3:
            raise Unclassified("The tables are too long for the model.") from None
        return summarise_table_group(client, system, {**sent, "tables": spread(sent["tables"], len(sent["tables"]) // 2)})
    return summary, log


# ------------------------------------------------------------------ step 3: act summary

ACT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["sentences"],
    "properties": {"sentences": {"type": "array", "minItems": 2, "maxItems": MAX_SENTENCES,
                                 "items": {"type": "string"}}},
}
END_OF_SENTENCE = re.compile(r"[.!?…][\"'”“»)\]]*$")
CELEX_IN_TEXT = re.compile(r"\s*\(?\b\d{5}[A-Z]{1,2}\d{4}\b\)?")
ENGLISH_ACT_WORDS = re.compile(r"\b(?:Regulation|Directive|Decision|Member States?|shall)\b")


def is_complete(sentence):
    return bool(END_OF_SENTENCE.search(sentence.strip()))


def validate_act(reply, source, celex="", final=False):
    """The sentences are taken as Qwen wrote them. Only an answer without sentences is asked once more."""
    sentences = reply.get("sentences") if isinstance(reply, dict) else None
    cleaned = [squash(sentence) for sentence in sentences or [] if isinstance(sentence, str) and squash(sentence)]
    if not cleaned:
        raise Unclassified("The reply must hold sentences, a list of full sentences.")
    return list(dict.fromkeys(cleaned))   # the same sentence twice is written once


def act_input(records, table_summaries=None):
    """The provisions shown to Qwen for the act summary.

    A group of tables counts as one entry (its saved group summary); a table that is not in a group counts once
    per section. The DIRECT ones come first (at most MAX_ACT_DIRECT), the other places are spread over the whole
    act, so that a long act is not described from its first articles only. Returned in the order of the act.
    """
    entries, grouped = [], set()
    for group in (table_summaries or {}).values():
        members = [r for r in records if r["target_id"] in set(group["members"])]
        if not members:
            continue
        grouped.update(r["target_id"] for r in members)
        summary = group["summary"]
        entries.append({"locator": group["base"], "level": "DIRECT" if any(r["level"] == "DIRECT" for r in members)
                        else "INDIRECT", "tables": len(members), "headline": summary["headline"],
                        "overview": summary["overview"], "points": [b["text"] for b in summary["bullets"]]})
    seen_sections = set()
    for record in sorted(records, key=lambda r: locator_key(r["target"])):
        base = table_base(record["target"])
        if record["target_id"] in grouped or (base and base in seen_sections):
            continue
        if base:
            seen_sections.add(base)
        summary = record["summary"]
        entries.append({"locator": plain_locator(record["target"]), "level": record["level"],
                        "headline": summary["headline"], "overview": summary.get("overview", ""),
                        "points": [bullet["text"] for bullet in summary["bullets"]]})
    entries.sort(key=lambda entry: locator_key(entry["locator"]))
    direct = [entry for entry in entries if entry["level"] == "DIRECT"][:MAX_ACT_DIRECT]
    rest = [entry for entry in entries if entry not in direct]
    chosen = direct + spread(rest, MAX_ACT_HEADLINES - len(direct))
    chosen.sort(key=lambda entry: locator_key(entry["locator"]))
    return [{key: value for key, value in entry.items() if key != "level"} for entry in chosen]


def summarise_act(client, system, celex, title, subject, provisions, total=None):
    kind = act_type(celex, OUTPUT_LANGUAGE or "de")
    sent = {"act": {"celex": celex, "title": title, **({"act_type": kind} if kind else {})}, "act_subject": subject,
            "provisions_total": total if total is not None else len(provisions),
            "provisions": provisions[:MAX_ACT_HEADLINES]}
    source, log = json.dumps(sent, ensure_ascii=False), []
    try:
        sentences = ask(client, system, sent, TASKS["act"], ACT_SCHEMA, "act_summary",
                        lambda reply, final=False: validate_act(reply, source, celex, final), log, 1600)
    except OverflowError:
        raise Unclassified("The act description is too long for the model.") from None
    return sentences, log


# ------------------------------------------------------------------ step 4: department summary

def department_schema(locators):
    return {
        "type": "object", "additionalProperties": False, "required": ["bullets"],
        "properties": {"bullets": {"type": "array", "minItems": 1, "maxItems": MAX_BULLETS, "items": {
            "type": "object", "additionalProperties": False, "required": ["kind", "text", "provisions"],
            "properties": {
                "kind": {"type": "string", "enum": list(DEPARTMENT_KINDS)},
                "text": {"type": "string"},
                "provisions": {"type": "array", "minItems": 1, "maxItems": 5,
                               "items": {"type": "string", "enum": list(locators)}},
            }}}},
    }


def validate_department(reply, provisions, source):
    """Taken as Qwen wrote it; only an answer without any point is asked once more."""
    locators = {p["locator"] for p in provisions}
    result, seen = [], set()
    for bullet in (reply.get("bullets") if isinstance(reply, dict) else None) or []:
        if not isinstance(bullet, dict) or not isinstance(bullet.get("text"), str) or not squash(bullet["text"]):
            continue
        text = squash(bullet["text"])
        if text.casefold() in seen:
            continue
        seen.add(text.casefold())
        kind = bullet.get("kind") if bullet.get("kind") in DEPARTMENT_KINDS else "WHY_DEPARTMENT"
        cited = [locator for locator in bullet.get("provisions") or [] if locator in locators]
        entry = {"kind": kind, "text": text, "provisions": list(dict.fromkeys(cited))}
        marks = number_marks([text], source)
        if marks:
            entry["number_check"] = marks
        result.append(entry)
    if not result:
        raise Unclassified("The reply must hold bullets with a text.")
    return sorted(result, key=lambda bullet: DEPARTMENT_KINDS.index(bullet["kind"]))


def card_input(departments, celex, title, act_sentences, provisions):
    """What Qwen sees for one department summary. DIRECT provisions come first."""
    ordered = sorted(provisions, key=lambda p: (p["level"] != "DIRECT", p["role"] != "primary"))
    shown = [{"locator": p["locator"], "level": p["level"], "why_relevant": p["why_relevant"],
              "indirect_path": p["indirect_path"], "to_confirm": p["to_confirm"], "domain": p["domain_title"],
              "why_this_domain": p["domain_reason"], "headline": p["headline"],
              "overview": p.get("overview", ""), "bullets": [bullet["text"] for bullet in p["bullets"]]} for p in ordered[:MAX_CARD_PROVISIONS]]
    sent = {"departments": departments, "act": {"celex": celex, "title": title, "summary": act_sentences},
            "provisions": shown}
    if len(ordered) > len(shown):
        sent["more_provisions"] = len(ordered) - len(shown)
    return sent


def summarise_department(client, system, sent):
    source, log = json.dumps(sent, ensure_ascii=False), []
    try:
        bullets = ask(client, system, sent, TASKS["department"], department_schema(
            [p["locator"] for p in sent["provisions"]]), "department_summary",
            lambda reply: validate_department(reply, sent["provisions"], source), log, 1600)
    except OverflowError:
        if len(sent["provisions"]) <= 3:
            raise Unclassified("The card is too long for the model.") from None
        shorter = {**sent, "provisions": sent["provisions"][:3],
                   "more_provisions": sent.get("more_provisions", 0) + len(sent["provisions"]) - 3}
        return summarise_department(client, system, shorter)
    return bullets, log


# ------------------------------------------------------------------ joining the saved results

def provision_facts(item, saved, result, unsure):
    """Why the provision was kept and where it was routed, from the earlier steps."""
    resolved = unsure.get(item["celex"], {}).get(item["target"]) if saved.get("level") == "UNSURE" else None
    details = saved.get("details", {})
    path = (resolved or {}).get("indirect_path") or details.get("indirect_path")
    return {
        "why_relevant": squash((resolved or saved).get("reason")),
        "indirect_path": path if path and path != "NONE" and item["level"] == "INDIRECT" else None,
        "to_confirm": squash(result.get("to_confirm") or item.get("to_confirm")),
    }


def groups_of(celex, records):
    """The cards of one act: provisions grouped by the set of departments that receive them together.

    Departments of the same domain get the same provisions, so their summary is written once.
    Returns {group key: {"departments": [...], "provisions": [...]}}.
    """
    by_department = defaultdict(list)
    for record in records:
        result = record["classification"]
        for role, domain, title, reason, departments in (
                ("primary", result["primary_domain"], result["primary_title"], result["primary_reason"],
                 result["departments"]),
                ("secondary", result["secondary_domain"], result["secondary_title"], result["secondary_reason"],
                 result["secondary_departments"])):
            if not domain:
                continue
            entry = {"locator": record["target"], "provision_id": record["target_id"], "level": record["level"],
                     "role": role, "domain": domain, "domain_title": title or domain, "domain_reason": reason,
                     "check": result.get("check", []), **record["facts"], **record["summary"]}
            for department in departments or [NO_DEPARTMENT]:
                by_department[department].append(entry)
    groups = {}
    for department, provisions in sorted(by_department.items()):
        key = sha([celex, sorted((p["provision_id"], p["role"]) for p in provisions)])[:16]
        group = groups.setdefault(key, {"departments": [], "provisions": provisions})
        group["departments"].append(department)
    return groups


# ------------------------------------------------------------------ tidying saved summaries (no Qwen)

# A definitions article: "Article 2 Definitions ...", "For the purposes of this Regulation, ... means ...".
DEFINITIONS = re.compile(r"^(?:Article\s+\S+\s+)?(?:Definitions?\b|.{0,120}?\bFor the purposes? of\b.{0,400}?\bmeans?\b)",
                         re.I | re.S)
# A "duty" that only says what the act or provision does: "Die Verordnung legt ... fest", "Die Tabelle enthält ...".
DESCRIBES_ONLY = re.compile(
    r"^(?:Die|Diese|Der|Dieser|Das|Dieses)\s+(?:\w+\s+){0,2}?(?:Entscheidung|Verordnung|Richtlinie|Vorschrift|"
    r"Tabelle|Anhang|Artikel|Bestimmung|Liste|Beschluss|Rechtsakt|Regelung)\s+(?:legt|enthält|regelt|beschreibt|"
    r"definiert|listet|bestimmt|umfasst|nennt|führt|gibt|sieht)\b", re.I)
# A sentence whose only point is that the text says nothing ("Es wird kein konkreter Zeitpunkt genannt.").
# The prompt forbids it. A sentence that goes on with a fact ("..., aber die Geldstrafe ...") is kept.
SAYS_NOTHING = re.compile(
    r"\bkein(?:e|en)?\s+(?:spezifisch|konkret|genau|ausdrücklich)\w*\s+(?:Zeitpunkt|Frist|Datum|Termin|Zeitraum)\b|"
    r"\b(?:wird|werden|ist|sind)\s+(?:hier\s+)?(?:nicht|keine)\s+(?:ausdrücklich\s+)?(?:genannt|angegeben|festgelegt|"
    r"erwähnt|bestimmt)\b", re.I)
GOES_ON = re.compile(r"\b(?:aber|jedoch|sondern|allerdings|stattdessen)\b|;", re.I)
# A point inside a definitions article that still states a real duty or prohibition keeps the kind DUTY.
STATES_DUTY = re.compile(r"\b(?:muss|müssen|darf|dürfen|verpflichtet|verboten|untersagt|hat\s+\w+\s+zu|haben\s+\w+\s+zu)\b",
                         re.I)
# A row of a table that repeats the same sentence in every EU language: "‘HR U dodiru ...’".
OTHER_LANGUAGE_ROW = re.compile(
    r"^[‘'\"„]?(?:BG|ES|CS|DA|ET|EL|FR|GA|HR|IT|LV|LT|HU|MT|NL|PL|PT|RO|SK|SL|FI|SV)\s+\S")


def is_other_language_row(record):
    """True for a provision that is only one language version of a phrase (not EN or DE)."""
    return bool(OTHER_LANGUAGE_ROW.match(record.get("text", "")))


# Fixed German legal terms and English act names that Qwen sometimes writes. Only exact, safe replacements.
TERM_FIXES = [
    (re.compile(r"\bErklärung (?:der|über die) Leistung\b"), "Leistungserklärung"),
    (re.compile(r"\bwettbewerbsgerechte(n?) Korrekturmaßnahme"), r"angemessene\1 Korrekturmaßnahme"),
    (re.compile(r"\bRegulation \(EU\)"), "Verordnung (EU)"),
    (re.compile(r"\bRegulation \((?:EC|EG)\)"), "Verordnung (EG)"),
    (re.compile(r"\bRegulation (?=(?:No\.?|Nr\.?)\s*\d)"), "Verordnung "),
    (re.compile(r"\bDirective (?=\(EU\)|\d)"), "Richtlinie "),
    (re.compile(r"\b(Verordnung|Richtlinie) \(EC\)"), r"\1 (EG)"),
    (re.compile(r"\bNo\.? (?=\d+/\d+)"), "Nr. "),
    (re.compile(r"\b(\d{2,4}/\d{1,4})/EEC\b"), r"\1/EWG"),
    (re.compile(r"\b(\d{2,4}/\d{1,4})/EC\b"), r"\1/EG"),
    (re.compile(r"\bMember States\b"), "Mitgliedstaaten"),
    (re.compile(r"\bLeistungsdeklaration"), "Leistungserklärung"),
    (re.compile(r"\bnicht-ferro\w*\s+Metall", re.I), "Nichteisenmetall"),
    (re.compile(r"\b(?:BAT|BVT)-associated emission levels?\b(?:\s*\((?:BAT|BVT)-AELs?\))?", re.I),
     "BVT-assoziierte Emissionswerte (BVT-AEL)"),
    (re.compile(r"\bassociated emission levels?\b", re.I), "assoziierte Emissionswerte"),
    (re.compile(r"\bBest Available Techniques?\b", re.I), "besten verfügbaren Techniken"),
    (re.compile(r"\bCommunity [Ee]co-[Mm]anagement and [Aa]udit [Ss]cheme\b(?:\s*\(EMAS\))?"),
     "Gemeinschaftssystem für Umweltmanagement und Umweltbetriebsprüfung (EMAS)"),
    (re.compile(r"\bCentral Administrator\b"), "Zentralverwalter"),
    (re.compile(r"\bRecovery and Resilience Facility\b"), "Aufbau- und Resilienzfazilität"),
    (re.compile(r"\bAgency for the Cooperation of Energy Regulators\b"),
     "Agentur für die Zusammenarbeit der Energieregulierungsbehörden"),
]
# Until 2014 a regulation is numbered number/year ("Verordnung (EU) Nr. 305/2011"); Qwen sometimes
# writes it the other way round ("Verordnung (EU) 2011/305"). Directives and decisions stay year/number.
# With "Nr." the number is already in the right order ("Nr. 1980/2000" is regulation 1980 of 2000) and
# is never changed; so is a pair whose second part could also be a year ("2008/97").
REVERSED_REGULATION = re.compile(
    r"\b(Verordnung)(\s+\((?:EU|EG|EWG|EC|EEC)\))?\s+(?!Nr\.)((?:19[5-9]\d|200\d|201[0-4]))/(\d{1,4})\b(?!/)")


def unreverse(match):
    number = match.group(4)
    if len(number) <= 2 or (len(number) == 4 and 1950 <= int(number) <= 2030):
        return match.group(0)
    part = (match.group(2) or "").replace("(EC)", "(EG)").replace("(EEC)", "(EWG)")
    return f"{match.group(1)}{part} Nr. {int(number)}/{match.group(3)}"


def fix_terms(text, language):
    if language != "de" or not text:
        return text
    for pattern, replacement in TERM_FIXES:
        text = pattern.sub(replacement, text)
    return REVERSED_REGULATION.sub(unreverse, text)


FEMININE_TYPES = ("Verordnung", "Richtlinie", "Entscheidung")


def fix_own_type(sentence, celex, language):
    """The wrong type word before the act's own number ("EU-Verordnung (EU) 2022/278" for a directive)
    is replaced, when both words take the same article (Verordnung, Richtlinie, Entscheidung)."""
    expected, numbers = act_type(celex, language), act_numbers(celex)
    if language != "de" or expected not in FEMININE_TYPES or not numbers:
        return sentence
    pattern = re.compile(rf"\b(?:EU-)?({'|'.join(FEMININE_TYPES)})\b(?=(?:\s*\((?:EU|EG|EWG)\))?(?:\s*Nr\.)?\s*"
                         rf"(?:{'|'.join(map(re.escape, numbers))})(?!\d)(?!/\d))")
    return pattern.sub(lambda match: expected if match.group(1) != expected else match.group(0), sentence)


# A WHO point that names no one: "Die Vorschrift betrifft die Herkunft von Elektroabfällen ...".
NOT_A_PERSON_START = re.compile(
    r"^(?:Die|Diese|Der|Dieser|Das|Dieses|The|This)\s+(?:\w+\s+)?(?:Vorschrift|Tabelle|Tabellen|Bestimmung|Regelung|"
    r"Anlage|Anhang|Liste|Artikel|provision|table|annex)\b", re.I)
PERSON = re.compile(  # also in compounds: Fahrzeughersteller, Anlagenbetreiber
    r"\b\w*(?:Administrator|Entität|Emittent|Organisation|Einrichtung|Akteur|Teilnehmer|Mitglied|Institut|Bank|"
    r"Versicherer|Firma|Begünstigte|Empfänger|Gesellschaft|Hersteller|Betreiber|Arbeitgeber|Unternehmen|Mitgliedstaat|Behörde|Kommission|Importeur|Einführer|Händler|"
    r"Wirtschaftsakteur|Anbieter|Lieferant|Erzeuger|Verwender|Anwender|Antragsteller|Inhaber|Auftraggeber|"
    r"Auftragnehmer|Prüfstelle|Stelle|Person|Partei|Unternehmer|Verkäufer|Käufer|Nutzer|Eigentümer|Bauherr|Planer|"
    r"Arbeitnehmer|Beschäftigte|Bevollmächtigte|Vertreter|Agentur|Ausschuss|Versorger|Verbraucher|Halter|Fahrer|"
    r"Beförderer|Spediteur|Anmelder|Ausführer|Exporteur|Netzbetreiber|employer|manufacturer|operator|authorit|"
    r"importer|undertaking|compan|Member State|Commission|person|supplier|distributor)", re.I)


def was_cut(summary):
    """True when a saved summary has a text that Qwen had to stop at its length limit."""
    texts = [(summary.get("overview", ""), LIMITS["overview"])]
    texts += [(bullet.get("text", ""), LIMITS["bullet"])
              for key in ("bullets", "more_bullets") for bullet in summary.get(key, [])]
    return (len(summary.get("headline", "")) >= LIMITS["headline"] - 1
            or any(cut_off(text, limit) != (text or "").strip() for text, limit in texts))


def act_has_error(saved, celex, language, has_tables):
    """True for a saved act summary of an older version that should be written again: a cut-off sentence,
    the wrong type of act, English act names, a CELEX number, or an act with a table group."""
    sentences = saved.get("sentences") or []
    return (not sentences or has_tables or not all(map(is_complete, sentences))
            or bool(wrong_type_word(sentences, celex, language))
            or (language == "de" and any(ENGLISH_ACT_WORDS.search(sentence) for sentence in sentences))
            or any(CELEX_IN_TEXT.search(sentence) for sentence in sentences))


def cut_off(text, limit):
    """For a text that Qwen had to stop at its length limit: the text up to its last complete sentence,
    or None when no complete sentence is left. Other texts come back unchanged."""
    text = (text or "").strip()
    if len(text) < limit - 20 or is_complete(text):
        return text                       # short points without a full stop ("Mitgliedstaaten") are fine
    ends = [match.end() for match in re.finditer(r"[.!?](?=\s+[A-ZÄÖÜ0-9„\"(])", text)]
    return text[:ends[-1]].strip() if ends and ends[-1] >= 40 else None


def names_no_one(text):
    return bool(NOT_A_PERSON_START.match(text or "")) and not PERSON.search(text or "")


def tidy_act_sentences(sentences, language, celex=""):
    """The saved act sentences with CELEX numbers, repeats and English act names corrected. Nothing is cut.
    Returns (sentences, number of repeats left out)."""
    kept = []
    for sentence in sentences:
        sentence = fix_own_type(fix_terms(squash(CELEX_IN_TEXT.sub("", sentence)), language), celex, language)
        if sentence and sentence not in kept:
            kept.append(sentence)
    return kept, len(sentences) - len(kept)


def tidy_summary(summary, text, language=None):
    """The saved summary with its point kinds corrected by rules, without asking Qwen again. No text is cut.

    - In a definitions article a DUTY that states no duty (no "muss", "darf", "verboten" ...) becomes OTHER:
      a definition is not a duty.
    - A DUTY that only says what the act does ("Die Verordnung legt ... fest") becomes OTHER.
    - A point whose only message is that something is not stated is left out (at least one point is kept).
    - A WHO point that names no one ("Die Vorschrift betrifft ...") becomes OTHER.
    - In German, fixed legal terms and English act names are corrected ("Erklärung der Leistung" ->
      "Leistungserklärung", "Regulation (EU)" -> "Verordnung (EU)").
    The counts are kept in summary["tidied"] so that the change can be checked.
    """
    definitions = bool(DEFINITIONS.match(text or ""))
    relabelled = removed = who = terms = 0
    result = dict(summary)
    for key in ("headline", "overview"):
        if isinstance(summary.get(key), str):
            fixed = fix_terms(summary[key], language)
            terms += fixed != summary[key]
            result[key] = fixed
    for key in ("bullets", "more_bullets"):
        if key not in summary:
            continue
        kept = []
        for bullet in summary[key]:
            bullet = dict(bullet)
            fixed = fix_terms(bullet.get("text", ""), language)
            terms += fixed != bullet.get("text", "")
            bullet["text"] = text_of = fixed
            one_sentence = len(re.findall(r"[.!?](?:\s|$)", text_of.strip())) <= 1
            if SAYS_NOTHING.search(text_of) and one_sentence and not GOES_ON.search(text_of):
                removed += 1
                continue
            if bullet.get("kind") == "DUTY" and (
                    (definitions and not STATES_DUTY.search(text_of)) or DESCRIBES_ONLY.match(text_of)):
                bullet["kind"] = "OTHER"
                relabelled += 1
            elif bullet.get("kind") == "WHO" and names_no_one(text_of):
                bullet["kind"] = "OTHER"
                who += 1
            kept.append(bullet)
        if key == "bullets" and not kept and summary[key]:
            kept, removed = [dict(summary[key][0])], removed - 1
        result[key] = sorted(kept, key=lambda b: BULLET_KINDS.index(b["kind"]) if b.get("kind") in BULLET_KINDS
                             else len(BULLET_KINDS))
    if relabelled or removed or who or terms:
        result["tidied"] = {"relabelled_to_other": relabelled, "removed": removed,
                            **({"who_to_other": who} if who else {}), **({"terms_fixed": terms} if terms else {})}
    return result


# ------------------------------------------------------------------ export

# "Aufgabe" is kept for a provision a department has confirmed; the AI only finds relevance.
LEVEL_LABEL = {"de": {"DIRECT": "Direkt relevant", "INDIRECT": "Indirekt relevant"},
               "en": {"DIRECT": "Directly relevant", "INDIRECT": "Indirectly relevant"}}
PART_TEXT = {
    "de": {"read": "Gelesen: Teil {read} von {total}.", "NOT_RELEVANT": "Teile {part}: bei der Prüfung nicht relevant.",
           "LIMIT": "Teile {part}: nicht gelesen (Grenze erreicht).", "TOO_LONG": "Teile {part}: zu lang, nicht gelesen.",
           "covers": "Überblick nur aus Teil {part}.", "more": "Weitere Punkte ({count})"},
    "en": {"read": "Read: part {read} of {total}.", "NOT_RELEVANT": "Parts {part}: not relevant in the screening.",
           "LIMIT": "Parts {part}: not read (limit reached).", "TOO_LONG": "Parts {part}: too long, not read.",
           "covers": "Overview from part {part} only.", "more": "More points ({count})"},
}
KIND_LABEL = {
    "de": {"WHO": "Wer", "DUTY": "Pflicht", "DEADLINE": "Frist", "CONDITION": "Bedingung", "OTHER": "Hinweis",
           "WHY_MAX_BOEGL": "Warum relevant", "WHY_DEPARTMENT": "Warum bei Ihnen", "ATTENTION": "Zuerst lesen",
           "TO_CONFIRM": "Bitte bestätigen"},
    "en": {"WHO": "Who", "DUTY": "Duty", "DEADLINE": "Deadline", "CONDITION": "Condition", "OTHER": "Note",
           "WHY_MAX_BOEGL": "Why relevant", "WHY_DEPARTMENT": "Why your department", "ATTENTION": "Read first",
           "TO_CONFIRM": "Please confirm"},
}
STYLE = """body{font-family:Segoe UI,Arial,sans-serif;margin:24px auto;max-width:980px;color:#1c2430;background:#f5f6f8}
h1{font-size:22px}.card{background:#fff;border:1px solid #d9dde3;border-radius:8px;padding:16px 20px;margin:16px 0}
.card h2{font-size:17px;margin:0 0 4px}.meta{color:#5b6675;font-size:13px;margin-bottom:8px}
.why{background:#f0f5fb;border-left:4px solid #2f6fb3;padding:8px 12px;margin:10px 0}
ul{margin:6px 0 6px 18px;padding:0}li{margin:3px 0}.kind{font-weight:600}
details{border-top:1px solid #e3e6eb;padding:8px 0}summary{cursor:pointer;font-weight:600}
.tag{display:inline-block;font-size:12px;border-radius:10px;padding:1px 9px;margin-left:8px;font-weight:600}
.DIRECT{background:#fde8d8;color:#8a3b00}.INDIRECT{background:#e6ecf2;color:#34495e}.table{background:#eef6ee;color:#2d5a2d}
.quote{color:#5b6675;font-size:13px;margin:2px 0 6px 0;border-left:2px solid #c9cfd8;padding-left:8px}
.parts{background:#f3f0fa;padding:4px 8px;border-radius:4px;font-size:13px;margin:4px 0}
.confirm{background:#fff7dd;padding:4px 8px;border-radius:4px;font-size:13px}.note{color:#7a8594;font-size:12px}
a{color:#2f6fb3}"""


TABLE_WORDS = {"de": {"tag": "{count} Tabellen", "each": "Die einzelnen Tabellen"},
               "en": {"tag": "{count} tables", "each": "The single tables"}}


def parts_note(p, language):
    words = PART_TEXT[language]
    lines = [words["read"].format(read=", ".join(p.get("parts_read", [])), total=p["parts_total"])]
    by_reason = defaultdict(list)
    for item in p.get("not_read", []):
        by_reason[item["reason"]].append(item["part"])
    for reason, numbers in by_reason.items():  # a list of 70 parts becomes one line
        shown = ", ".join(numbers[:6]) + (f" … (+{len(numbers) - 6})" if len(numbers) > 6 else "")
        lines.append(words[reason].format(part=shown))
    if p.get("overview_covers"):
        lines.append(words["covers"].format(part=p["overview_covers"]))
    return " ".join(lines)


def card_html(card, language):
    e, kinds = html.escape, KIND_LABEL[language]
    out = [f'<div class="card"><h2>{e(card["title"] or card["celex"])}</h2>',
           f'<div class="meta">{e(card["celex"])} · {len(card["provisions"])} · '
           f'<a href="{e(card["eurlex_url"])}">EUR-Lex</a> · <a href="{e(card["pdf_url"])}">PDF</a></div>',
           f'<p>{e(" ".join(card["act_summary"]))}</p>']
    if card["department_summary"]:
        out.append('<div class="why"><ul>' + "".join(
            f'<li><span class="kind">{e(kinds[b["kind"]])}:</span> {e(b["text"])} '
            f'<span class="note">[{e(", ".join(b["provisions"]))}]</span></li>'
            for b in card["department_summary"]) + "</ul></div>")
    for group in card.get("table_groups", []):
        out.append(f'<details><summary>{e(group["locator"])} — {e(group["headline"])}'
                   f'<span class="tag table">{e(TABLE_WORDS[language]["tag"].format(count=len(group["members"])))}</span>'
                   f'</summary><p>{e(group["overview"])}</p><ul>' + "".join(
                       f'<li><span class="kind">{e(kinds[b["kind"]])}:</span> {e(b["text"])}</li>' for b in group["bullets"])
                   + f'</ul><details class="more"><summary>{e(TABLE_WORDS[language]["each"])}</summary><ul>' + "".join(
                       f'<li>{e(p["locator"])} — {e(p["headline"])}</li>' for p in card["provisions"]
                       if p.get("table_group") == group["key"]) + "</ul></details></details>")
    for p in card["provisions"]:
        if p.get("table_group") in {group["key"] for group in card.get("table_groups", [])}:
            continue  # shown once in its table group
        out.append(f'<details><summary>{e(p["locator"])} — {e(p["headline"])}'
                   f'<span class="tag {e(p["level"])}">{e(LEVEL_LABEL[language][p["level"]])}</span></summary><p>{e(p.get("overview", ""))}</p><ul>')
        for b in p["bullets"]:
            quotes = "".join(f'<div class="quote">{e(q)}</div>' for q in b.get("quotes", []))
            out.append(f'<li><span class="kind">{e(kinds[b["kind"]])}:</span> {e(b["text"])}{quotes}</li>')
        out.append("</ul>")
        if p.get("more_bullets"):
            words = PART_TEXT[language]
            out.append(f'<details class="more"><summary>{e(words["more"].format(count=len(p["more_bullets"])))}'
                       '</summary><ul>' + "".join(
                           f'<li><span class="kind">{e(kinds[b["kind"]])}:</span> {e(b["text"])}'
                           + "".join(f'<div class="quote">{e(q)}</div>' for q in b.get("quotes", [])) + "</li>"
                           for b in p["more_bullets"]) + "</ul></details>")
        if p.get("parts_total"):
            out.append(f'<div class="parts">{e(parts_note(p, language))}</div>')
        if p.get("to_confirm"):
            out.append(f'<div class="confirm">{e(kinds["TO_CONFIRM"])}: {e(p["to_confirm"])}</div>')
        out.append(f'<div class="note">{e(p["domain_title"])}</div></details>')
    out.append(f'<div class="note">{e(NOTE[language])}</div></div>')
    return "".join(out)


def saved_table_groups(out):
    """{celex: {key: saved group}} for the table groups written with the current TABLE_VERSION."""
    found = defaultdict(dict)
    folder = out / "tables"
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        saved = read_json(path)
        if saved.get("version") == TABLE_VERSION and saved.get("celex") and saved.get("key"):
            found[saved["celex"]][saved["key"]] = saved
    return found


def export(out, planned, language):
    """Write the inbox files, the preview pages, review.csv and summary.json from what is saved."""
    by_act, rows, failures = defaultdict(list), [], []
    tidy_counts = Counter()
    for item in planned:
        path = record_path(out / "provisions", item)
        if not path.is_file():
            continue
        record = read_json(path)
        if "summary" not in record:
            failures.append({"celex": record["celex"], "target": record["target"], "error": record.get("failure")})
            continue
        if is_other_language_row(record):
            tidy_counts["other_language_rows_left_out"] += 1
            continue
        record["summary"] = tidy_summary(record["summary"], record.get("text", ""), language)
        for key, value in record["summary"].get("tidied", {}).items():
            tidy_counts[f"points_{key}"] += value
        by_act[record["celex"]].append(record)
        summary, result = record["summary"], record.get("classification", {})
        rows.append([record["celex"], record["target"], record["level"], summary["headline"], summary.get("overview", ""),
                     " | ".join(f'{b["kind"]}: {b["text"]}' for b in summary["bullets"]),
                     " | ".join(f'{b["kind"]}: {b["text"]}' for b in summary.get("more_bullets", [])),
                     parts_note(summary, "en") if summary.get("parts_total") else "",
                     result.get("primary_title", ""),
                     ", ".join([*result.get("departments", []), *result.get("secondary_departments", [])]),
                     record.get("text", "")[:400]])
    inbox, cards_without_summary = defaultdict(list), 0
    tables = saved_table_groups(out)
    table_rows, tables_failed = [], 0
    for celex, records in sorted(by_act.items()):
        act_file = out / "acts" / f"{safe_name(celex)}.json"
        act = read_json(act_file) if act_file.is_file() else {}
        act_sentences, cut = tidy_act_sentences(act.get("sentences", []), language, celex)
        tidy_counts["act_sentences_repeated"] += cut
        act_tables = {}
        for key, group in table_groups_of(celex, records).items():
            saved_group = tables.get(celex, {}).get(key)
            members = [r["target_id"] for r in group["records"]]
            if not saved_group or saved_group.get("members") != members or "summary" not in saved_group:
                tables_failed += bool(saved_group and "failure" in saved_group)
                continue
            act_tables[key] = {"key": key, "locator": group["base"], **saved_group["summary"], "ids": set(members)}
            table_rows.append([celex, group["base"], len(members), saved_group["summary"]["headline"],
                               saved_group["summary"]["overview"],
                               " | ".join(f'{b["kind"]}: {b["text"]}' for b in saved_group["summary"]["bullets"]),
                               " | ".join(plain_locator(r["target"]) for r in group["records"][:20])])
        for key, group in groups_of(celex, records).items():
            saved = out / "departments" / f"{safe_name(celex)}_{key}.json"
            bullets = read_json(saved).get("bullets", []) if saved.is_file() else []
            cards_without_summary += not bullets and group["departments"] != [NO_DEPARTMENT]
            ordered = sorted(group["provisions"], key=lambda p: (p["level"] != "DIRECT", p["locator"]))
            card_tables = []
            for table in act_tables.values():
                inside = [p for p in ordered if p["provision_id"] in table["ids"]]
                if len(inside) < 2:
                    continue  # one table alone is shown as it is
                for p in inside:
                    p["table_group"] = table["key"]
                card_tables.append({key: value for key, value in table.items() if key != "ids"}
                                   | {"members": [p["locator"] for p in inside]})
            for department in group["departments"]:
                inbox[department].append({
                    "celex": celex, "title": records[0].get("title", ""), **act_links(celex),
                    "act_summary": act_sentences, "department_summary": bullets, "table_groups": card_tables,
                    "direct": sum(p["level"] == "DIRECT" for p in ordered),
                    "indirect": sum(p["level"] == "INDIRECT" for p in ordered),
                    "provisions": ordered, "note": NOTE[language]})
    index = []
    for department, cards in sorted(inbox.items()):
        cards.sort(key=lambda card: (-card["direct"], card["celex"]))
        name = safe_name(department)
        write_json(out / "inbox" / f"{name}.json", {"department": department, "acts": len(cards), "cards": cards})
        page = (f'<!doctype html><html lang="{language}"><head><meta charset="utf-8"><title>{html.escape(department)}'
                f'</title><style>{STYLE}</style></head><body><p><a href="index.html">&larr;</a></p>'
                f'<h1>{html.escape(department)} · {len(cards)}</h1>'
                + "".join(card_html(card, language) for card in cards) + "</body></html>")
        (out / "preview").mkdir(parents=True, exist_ok=True)
        (out / "preview" / f"{name}.html").write_text(page, encoding="utf-8")
        index.append((department, name, len(cards), sum(card["direct"] for card in cards)))
    if index:
        listing = "".join(f'<li><a href="{name}.html">{html.escape(department)}</a> — {acts} · DIRECT {direct}</li>'
                          for department, name, acts, direct in index)
        (out / "preview" / "index.html").write_text(
            f'<!doctype html><html lang="{language}"><head><meta charset="utf-8"><title>Legal Radar</title>'
            f'<style>{STYLE}</style></head><body><h1>Legal Radar</h1><ul>{listing}</ul></body></html>',
            encoding="utf-8")
    with (out / "review.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["celex", "provision", "level", "headline", "overview", "bullets", "more_bullets",
                         "parts", "domain", "departments", "text_start", "summary correct (yes/no)",
                         "missing or wrong", "department right (yes/no)", "comment"])
        writer.writerows(rows)
    with (out / "review_tables.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["celex", "section", "tables", "headline", "overview", "bullets", "first tables",
                         "summary correct (yes/no)", "missing or wrong", "comment"])
        writer.writerows(table_rows)
    bullets = [len(record["summary"]["bullets"]) for records in by_act.values() for record in records]
    summary = {
        "language": language, "provisions_planned": len(planned), "provisions_summarised": len(rows),
        "provisions_failed": len(failures), "provisions_open": len(planned) - len(rows) - len(failures),
        "acts": len(by_act), "departments": len(inbox), "cards": sum(len(cards) for cards in inbox.values()),
        "cards_without_department_summary": cards_without_summary,
        "bullets_per_provision": round(sum(bullets) / len(bullets), 2) if bullets else 0,
        "provisions_with_four_bullets": sum(count == MAX_BULLETS for count in bullets),
        "table_groups": len(table_rows), "tables_in_groups": sum(row[2] for row in table_rows),
        "table_groups_failed": tables_failed,
        "tidied": dict(tidy_counts), "failures": failures,
    }
    write_json(out / "summary.json", summary)
    return summary


# ------------------------------------------------------------------ the run

def provision_fingerprint(systems, language):
    return sha([{name: systems[name] for name in PROVISION_PROMPTS}, {name: TASKS[name] for name in PROVISION_PROMPTS},
                provision_schema(["P1"]), MERGE_SCHEMA, MAX_PARTS, MAX_PART_REQUESTS, language])


def same_provision_prompts(saved, systems, fingerprint, language):
    """True when the folder was started with the same provision prompts and language. The saved prompts are
    compared, so that a change of the code (length limits, checks) does not need a new folder."""
    prompts = saved.get("system_prompts", {})
    return saved.get("language") == language and all(prompts.get(name) == systems[name] for name in PROVISION_PROMPTS)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=Path, nargs="+", default=DEFAULT_RUNS)
    parser.add_argument("--unsure-dir", type=Path, default=DEFAULT_UNSURE)
    parser.add_argument("--classification-dir", type=Path, default=DEFAULT_CLASSIFICATION)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--levels", nargs="+", choices=KEPT, default=list(KEPT))
    parser.add_argument("--language", choices=tuple(LANGUAGES), default="de")
    parser.add_argument("--sample", type=int, help="Summarise only this many provisions (a fixed choice).")
    parser.add_argument("--celex", nargs="+", default=[], help="Also include every provision of these acts.")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--count-only", action="store_true")
    args = parser.parse_args()
    global OUTPUT_LANGUAGE
    OUTPUT_LANGUAGE = args.language
    out = args.output_dir.resolve()

    try:
        systems = {name: system_prompt(name, args.language) for name in PROMPTS}
        classified = read_json(args.classification_dir / "results.json")
        act_domains = read_json(args.classification_dir / "acts.json")
        unsure_file = args.unsure_dir / "results.json"
        unsure = read_json(unsure_file) if unsure_file.is_file() else {}
        items, _ = collect([run.resolve() for run in args.runs], args.unsure_dir)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 1
    with_domain = [item for item in items if item["target"] in classified.get(item["celex"], {})]
    eligible = [item for item in with_domain if item["level"] in args.levels]
    print(f"{len(items):,} relevant provisions, {len(with_domain):,} of them classified. "
          f"{len(eligible):,} with level {' or '.join(args.levels)}.", flush=True)
    if args.count_only:
        return 0
    if not eligible:
        print("Nothing to summarise. Run the classification first.", file=sys.stderr)
        return 1

    # Only the provision prompts decide whether a folder can be continued. A new act, table or department prompt
    # rewrites that step in the same folder (see ACT_VERSION, TABLE_VERSION) and keeps the provision summaries.
    fingerprint = provision_fingerprint(systems, args.language)
    config = out / "run_config.json"
    if config.is_file() and not same_provision_prompts(read_json(config), systems, fingerprint, args.language):
        print("The provision prompts or the language changed since this folder was started. "
              "Use a new --output-dir, for example runs/eurlex_summaries_v2.", file=sys.stderr)
        return 1
    write_json(config, {"provision_fingerprint": fingerprint, "language": args.language,
                        "classification": str(args.classification_dir), "system_prompts": systems,
                        "versions": {"act": ACT_VERSION, "tables": TABLE_VERSION}})
    planned = sample(eligible, args.sample) if args.sample else list(eligible)
    chosen = {(item["celex"], item["target"]) for item in planned}
    planned += [item for item in eligible if item["celex"] in args.celex
                and (item["celex"], item["target"]) not in chosen]
    planned.sort(key=lambda item: (item["celex"], item["target"], item["target_id"]))
    print(f"Planned: {len(planned):,} provisions in {len({item['celex'] for item in planned}):,} acts.", flush=True)

    client = QwenClient.from_env(args.env_file, retries=1)
    workers = max(1, min(args.workers, MAX_WORKERS))
    provisions_dir = out / "provisions"

    def saved_summary(item):
        path = record_path(provisions_dir, item)
        record = read_json(path) if path.is_file() else {}
        return record if "summary" in record else None

    def needs_summary(item):
        """No summary yet, or a summary of an earlier version with a text cut off at the length limit
        (asked once more; the earlier summary is kept if the new request fails)."""
        record = saved_summary(item)
        return record is None or (record.get("redo") != CUT_REDO and was_cut(record["summary"]))

    def open_provisions():
        todo = [item for item in planned if needs_summary(item)]
        return todo, len(planned) - len(todo)

    def provision_worker(progress):
        def work(item):
            # Written without length limits: marked so that it is never asked again because of its length.
            record = {key: item[key] for key in ("celex", "target", "kind", "target_id", "level")} | {"redo": CUT_REDO}
            earlier = saved_summary(item)
            failed = False
            try:
                saved = read_json(item["source"])
                result = classified[item["celex"]][item["target"]]
                record["title"] = squash(saved["evidence_pack"]["document"].get("title"))
                record["text"] = squash(" ".join(target_text(saved["evidence_pack"]["provision"])))[:600]
                record["classification"] = result
                record["facts"] = provision_facts(item, saved, result, unsure)
                record["summary"], record["audit"] = summarise_provision(client, systems["provision"], saved,
                                                                         systems["merge"])
            except ConnectionLost:
                raise
            except Unclassified as exc:
                record["failure"], failed = str(exc), True
            except Exception as exc:  # noqa: BLE001 - one unreadable provision must not stop the run
                record["failure"], failed = f"{type(exc).__name__}: {exc}"[:400], True
            if failed and earlier:     # the new request failed: keep the earlier summary, do not ask again
                record = {**earlier, "redo": CUT_REDO, "redo_failure": record["failure"]}
                failed = False
            elif failed:
                record.pop("summary", None)
                print(f"Could not summarise {item['celex']} {item['target']}: {record['failure']}", flush=True)
            write_json(record_path(provisions_dir, item), record)
            progress.step(failed)
        return work

    def act_records():
        """The saved summaries by act, tidied as on the cards. Language rows are left out here as on the cards,
        so that the cards of the run and of the export are the same groups."""
        acts = defaultdict(list)
        for item in planned:
            if (record := saved_summary(item)) and not is_other_language_row(record):
                record["summary"] = tidy_summary(record["summary"], record.get("text", ""), args.language)
                acts[item["celex"]].append(record)
        return acts

    tables_dir = out / "tables"

    def table_file(celex, key):
        return tables_dir / f"{safe_name(celex)}_{key}.json"

    def open_tables():
        todo, total = [], 0
        for celex, records in sorted(act_records().items()):
            for key, group in table_groups_of(celex, records).items():
                total += 1
                path = table_file(celex, key)
                saved = read_json(path) if path.is_file() else {}
                members = [r["target_id"] for r in group["records"]]
                if not ("summary" in saved and saved.get("version") == TABLE_VERSION and saved.get("members") == members):
                    todo.append((celex, key, group, records[0].get("title", "")))
        return todo, total - len(todo)

    def table_worker(progress):
        def work(job):
            celex, key, group, title = job
            record = {"celex": celex, "key": key, "base": group["base"], "version": TABLE_VERSION,
                      "members": [r["target_id"] for r in group["records"]]}
            failed = False
            try:
                record["summary"], record["requests"] = summarise_table_group(
                    client, systems["table"], table_input(celex, title, group["base"], group["records"]))
            except ConnectionLost:
                raise
            except Unclassified as exc:
                record["failure"], failed = str(exc), True
            except Exception as exc:  # noqa: BLE001
                record["failure"], failed = f"{type(exc).__name__}: {exc}"[:400], True
            if failed:
                print(f"Could not summarise the tables {celex} {group['base']}: {record['failure']}", flush=True)
            write_json(table_file(celex, key), record)
            progress.step(failed)
        return work

    def act_is_current(path, celex, records):
        """Written with this version for the same provisions; an act summary of an earlier version is kept when
        it has none of the errors that the new version fixes."""
        saved = read_json(path) if path.is_file() else {}
        if "sentences" not in saved or saved.get("provisions") != len(records):
            return False
        if saved.get("version") == ACT_VERSION:
            return True
        return "version" not in saved and not act_has_error(
            saved, celex, args.language, bool(table_groups_of(celex, records)))

    def open_acts():
        acts, tables = act_records(), saved_table_groups(out)
        todo = [(celex, records, {key: saved for key, saved in tables.get(celex, {}).items() if "summary" in saved})
                for celex, records in sorted(acts.items())
                if not act_is_current(out / "acts" / f"{safe_name(celex)}.json", celex, records)]
        return todo, len(acts) - len(todo)

    def act_worker(progress):
        def work(job):
            celex, records, tables = job
            record, failed = {"celex": celex, "provisions": len(records), "version": ACT_VERSION}, False
            try:
                record["sentences"], record["requests"] = summarise_act(
                    client, systems["act"], celex, records[0].get("title", ""),
                    (act_domains.get(celex) or {}).get("act_subject", ""), act_input(records, tables), len(records))
            except ConnectionLost:
                raise
            except Unclassified as exc:
                record["failure"], failed = str(exc), True
            except Exception as exc:  # noqa: BLE001
                record["failure"], failed = f"{type(exc).__name__}: {exc}"[:400], True
            if failed:
                print(f"Could not summarise the act {celex}: {record['failure']}", flush=True)
            write_json(out / "acts" / f"{safe_name(celex)}.json", record)
            progress.step(failed)
        return work

    def open_cards():
        todo, total = [], 0
        for celex, records in sorted(act_records().items()):
            for key, group in groups_of(celex, records).items():
                if group["departments"] == [NO_DEPARTMENT]:
                    continue  # nobody to explain it to yet
                total += 1
                path = out / "departments" / f"{safe_name(celex)}_{key}.json"
                if not (path.is_file() and "bullets" in read_json(path)):
                    todo.append((celex, key, group, records[0].get("title", "")))
        return todo, total - len(todo)

    def card_worker(progress):
        def work(job):
            celex, key, group, title = job
            act_file = out / "acts" / f"{safe_name(celex)}.json"
            sentences = read_json(act_file).get("sentences", []) if act_file.is_file() else []
            departments = [d for d in group["departments"] if d != NO_DEPARTMENT]
            record, failed = {"celex": celex, "departments": departments}, False
            try:
                sent = card_input(departments, celex, title, sentences, group["provisions"])
                record["bullets"], record["requests"] = summarise_department(client, systems["department"], sent)
            except ConnectionLost:
                raise
            except Unclassified as exc:
                record["failure"], failed = str(exc), True
            except Exception as exc:  # noqa: BLE001
                record["failure"], failed = f"{type(exc).__name__}: {exc}"[:400], True
            if failed:
                print(f"Could not write the department summary for {celex}: {record['failure']}", flush=True)
            write_json(out / "departments" / f"{safe_name(celex)}_{key}.json", record)
            progress.step(failed)
        return work

    try:
        ok, error = reachable(client)   # also tells the client the address and model name
        if not ok:
            wait_until_reachable(client, error)
        save = lambda: export(out, planned, args.language)  # noqa: E731
        run_until_done(client, "Provisions", open_provisions, provision_worker, workers, after_pass=save)
        run_until_done(client, "Tables", open_tables, table_worker, workers)
        run_until_done(client, "Acts", open_acts, act_worker, workers)
        run_until_done(client, "Cards", open_cards, card_worker, workers, after_pass=save)
    except KeyboardInterrupt:
        export(out, planned, args.language)
        print("\nStopped by Ctrl+C. Run the same command to continue.", flush=True)
        return 130

    summary = export(out, planned, args.language)
    print(f"\nFinished: {summary['provisions_summarised']:,} of {len(planned):,} provisions summarised, "
          f"{summary['provisions_failed']:,} failed. {summary['cards']:,} cards for "
          f"{summary['departments']:,} departments, {summary['cards_without_department_summary']:,} without "
          f"a department summary.")
    print(f"Open in a browser: {out / 'preview' / 'index.html'}\nTo check by hand: {out / 'review.csv'}\n"
          f"For the UI: {out / 'inbox'}")
    if summary["provisions_failed"] or summary["cards_without_department_summary"]:
        print("Run the same command again to retry what failed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
