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


def validate_provision(reply, passages, source, locator=""):
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
    check_language([squash(headline), squash(overview), *(bullet["text"] for bullet in result)])
    check_numbers([squash(headline), squash(overview), *(bullet["text"] for bullet in result)], source)
    for bullet in result:
        cite_numbers(bullet, passages, locator)
    return {"headline": squash(headline), "overview": squash(overview), "bullets": result}


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
                         lambda reply: validate_provision(reply, passages, source, locator), log, 1100)
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


# ------------------------------------------------------------------ step 2: act summary

ACT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["sentences"],
    "properties": {"sentences": {"type": "array", "minItems": 2, "maxItems": MAX_SENTENCES,
                                 "items": {"type": "string", "maxLength": LIMITS["sentence"]}}},
}


def validate_act(reply, source):
    sentences = reply.get("sentences") if isinstance(reply, dict) and set(reply) == {"sentences"} else None
    if not isinstance(sentences, list) or not 2 <= len(sentences) <= MAX_SENTENCES:
        raise Unclassified("The reply holds exactly sentences, a list of two to five full sentences.")
    cleaned = [squash(sentence) for sentence in sentences if isinstance(sentence, str) and squash(sentence)]
    if len(cleaned) != len(sentences) or any(len(sentence) > LIMITS["sentence"] for sentence in cleaned):
        raise Unclassified(f"Every sentence is a text of 1 to {LIMITS['sentence']} characters.")
    cleaned = without_empty_statements(cleaned, lambda sentence: sentence)
    check_language(cleaned)
    check_numbers(cleaned, source)
    return cleaned


def summarise_act(client, system, celex, title, subject, headlines):
    sent = {"act": {"celex": celex, "title": title}, "act_subject": subject,
            "provisions": headlines[:MAX_ACT_HEADLINES]}
    source, log = json.dumps(sent, ensure_ascii=False), []
    try:
        sentences = ask(client, system, sent, TASKS["act"], ACT_SCHEMA, "act_summary",
                        lambda reply: validate_act(reply, source), log, 700)
    except OverflowError:
        raise Unclassified("The act description is too long for the model.") from None
    return sentences, log


# ------------------------------------------------------------------ step 3: department summary

def department_schema(locators):
    return {
        "type": "object", "additionalProperties": False, "required": ["bullets"],
        "properties": {"bullets": {"type": "array", "minItems": 1, "maxItems": MAX_BULLETS, "items": {
            "type": "object", "additionalProperties": False, "required": ["kind", "text", "provisions"],
            "properties": {
                "kind": {"type": "string", "enum": list(DEPARTMENT_KINDS)},
                "text": {"type": "string", "maxLength": LIMITS["department_bullet"]},
                "provisions": {"type": "array", "minItems": 1, "maxItems": 5,
                               "items": {"type": "string", "enum": list(locators)}},
            }}}},
    }


def validate_department(reply, provisions, source):
    bullets = reply.get("bullets") if isinstance(reply, dict) and set(reply) == {"bullets"} else None
    if not isinstance(bullets, list) or not 1 <= len(bullets) <= MAX_BULLETS:
        raise Unclassified(f"The reply holds exactly bullets, a list of 1 to {MAX_BULLETS} entries.")
    open_questions = {p["locator"] for p in provisions if p.get("to_confirm")}
    locators = {p["locator"] for p in provisions}
    result, seen = [], set()
    for bullet in bullets:
        if not isinstance(bullet, dict) or set(bullet) != {"kind", "text", "provisions"}:
            raise Unclassified("Every bullet holds exactly kind, text and provisions.")
        text = squash(bullet["text"]) if isinstance(bullet["text"], str) else ""
        if bullet["kind"] not in DEPARTMENT_KINDS or not text or len(text) > LIMITS["department_bullet"]:
            raise Unclassified(f"A bullet needs a kind from {', '.join(DEPARTMENT_KINDS)} and a text of at most "
                               f"{LIMITS['department_bullet']} characters.")
        cited = bullet["provisions"]
        if not isinstance(cited, list) or not cited or any(locator not in locators for locator in cited):
            raise Unclassified("provisions must name locators from the input, for example Article 5.")
        if bullet["kind"] == "TO_CONFIRM" and not open_questions & set(cited):
            continue  # an open question that the input does not contain is dropped
        if text.casefold() in seen:
            continue
        seen.add(text.casefold())
        result.append({"kind": bullet["kind"], "text": text, "provisions": list(dict.fromkeys(cited))})
    if not result:
        raise Unclassified("No usable bullet. Write at least one bullet of kind WHY_MAX_BOEGL or WHY_DEPARTMENT.")
    result = without_empty_statements(result, lambda bullet: bullet["text"])
    check_language([bullet["text"] for bullet in result])
    check_numbers([bullet["text"] for bullet in result], source)
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
            lambda reply: validate_department(reply, sent["provisions"], source), log, 1000)
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


# ------------------------------------------------------------------ export

# "Aufgabe" is kept for a provision a department has confirmed; the AI only finds relevance.
LEVEL_LABEL = {"de": {"DIRECT": "Direkt relevant", "INDIRECT": "Indirekt relevant"},
               "en": {"DIRECT": "Directly relevant", "INDIRECT": "Indirectly relevant"}}
PART_TEXT = {
    "de": {"read": "Gelesen: Teil {read} von {total}.", "NOT_RELEVANT": "Teil {part}: bei der Prüfung nicht relevant",
           "LIMIT": "Teil {part}: nicht gelesen (Grenze erreicht)", "TOO_LONG": "Teil {part}: zu lang, nicht gelesen",
           "covers": "Überblick nur aus Teil {part}.", "more": "Weitere Punkte ({count})"},
    "en": {"read": "Read: part {read} of {total}.", "NOT_RELEVANT": "Part {part}: not relevant in the screening",
           "LIMIT": "Part {part}: not read (limit reached)", "TOO_LONG": "Part {part}: too long, not read",
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
.DIRECT{background:#fde8d8;color:#8a3b00}.INDIRECT{background:#e6ecf2;color:#34495e}
.quote{color:#5b6675;font-size:13px;margin:2px 0 6px 0;border-left:2px solid #c9cfd8;padding-left:8px}
.parts{background:#f3f0fa;padding:4px 8px;border-radius:4px;font-size:13px;margin:4px 0}
.confirm{background:#fff7dd;padding:4px 8px;border-radius:4px;font-size:13px}.note{color:#7a8594;font-size:12px}
a{color:#2f6fb3}"""


def parts_note(p, language):
    words = PART_TEXT[language]
    lines = [words["read"].format(read=", ".join(p.get("parts_read", [])), total=p["parts_total"])]
    lines += [words[item["reason"]].format(part=item["part"]) for item in p.get("not_read", [])]
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
    for p in card["provisions"]:
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


def export(out, planned, language):
    """Write the inbox files, the preview pages, review.csv and summary.json from what is saved."""
    by_act, rows, failures = defaultdict(list), [], []
    for item in planned:
        path = record_path(out / "provisions", item)
        if not path.is_file():
            continue
        record = read_json(path)
        if "summary" not in record:
            failures.append({"celex": record["celex"], "target": record["target"], "error": record.get("failure")})
            continue
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
    for celex, records in sorted(by_act.items()):
        act = read_json(out / "acts" / f"{safe_name(celex)}.json") if (out / "acts" / f"{safe_name(celex)}.json").is_file() else {}
        for key, group in groups_of(celex, records).items():
            saved = out / "departments" / f"{safe_name(celex)}_{key}.json"
            bullets = read_json(saved).get("bullets", []) if saved.is_file() else []
            cards_without_summary += not bullets and group["departments"] != [NO_DEPARTMENT]
            ordered = sorted(group["provisions"], key=lambda p: (p["level"] != "DIRECT", p["locator"]))
            for department in group["departments"]:
                inbox[department].append({
                    "celex": celex, "title": records[0].get("title", ""), **act_links(celex),
                    "act_summary": act.get("sentences", []), "department_summary": bullets,
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
    bullets = [len(record["summary"]["bullets"]) for records in by_act.values() for record in records]
    summary = {
        "language": language, "provisions_planned": len(planned), "provisions_summarised": len(rows),
        "provisions_failed": len(failures), "provisions_open": len(planned) - len(rows) - len(failures),
        "acts": len(by_act), "departments": len(inbox), "cards": sum(len(cards) for cards in inbox.values()),
        "cards_without_department_summary": cards_without_summary,
        "bullets_per_provision": round(sum(bullets) / len(bullets), 2) if bullets else 0,
        "provisions_with_four_bullets": sum(count == MAX_BULLETS for count in bullets), "failures": failures,
    }
    write_json(out / "summary.json", summary)
    return summary


# ------------------------------------------------------------------ the run

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

    fingerprint = sha([systems, TASKS, provision_schema(["P1"]), MERGE_SCHEMA, ACT_SCHEMA, department_schema(["A"]),
                       MAX_PARTS, MAX_PART_REQUESTS, MAX_CARD_PROVISIONS, args.language])
    config = out / "run_config.json"
    if config.is_file() and read_json(config).get("fingerprint") != fingerprint:
        print("The prompts or the language changed since this folder was started. "
              "Use a new --output-dir, for example runs/eurlex_summaries_v2.", file=sys.stderr)
        return 1
    write_json(config, {"fingerprint": fingerprint, "language": args.language,
                        "classification": str(args.classification_dir), "system_prompts": systems})
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

    def open_provisions():
        todo = [item for item in planned if saved_summary(item) is None]
        return todo, len(planned) - len(todo)

    def provision_worker(progress):
        def work(item):
            record = {key: item[key] for key in ("celex", "target", "kind", "target_id", "level")}
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
            if failed:
                record.pop("summary", None)
                print(f"Could not summarise {item['celex']} {item['target']}: {record['failure']}", flush=True)
            write_json(record_path(provisions_dir, item), record)
            progress.step(failed)
        return work

    def act_records():
        acts = defaultdict(list)
        for item in planned:
            if record := saved_summary(item):
                acts[item["celex"]].append(record)
        return acts

    def act_is_current(path, provisions):
        saved = read_json(path) if path.is_file() else {}
        return "sentences" in saved and saved.get("provisions") == provisions

    def open_acts():
        acts = act_records()
        todo = [(celex, records) for celex, records in sorted(acts.items())
                if not act_is_current(out / "acts" / f"{safe_name(celex)}.json", len(records))]
        return todo, len(acts) - len(todo)

    def act_worker(progress):
        def work(job):
            celex, records = job
            record, failed = {"celex": celex, "provisions": len(records)}, False
            records = sorted(records, key=lambda r: r["level"] != "DIRECT")
            headlines = [{"locator": r["target"], "headline": r["summary"]["headline"], "overview": r["summary"].get("overview", ""),
                          "points": [bullet["text"] for bullet in r["summary"]["bullets"]]} for r in records]
            try:
                record["sentences"], record["requests"] = summarise_act(
                    client, systems["act"], celex, records[0].get("title", ""),
                    (act_domains.get(celex) or {}).get("act_subject", ""), headlines)
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
