"""Retry recorded provision failures in an existing summary run."""
import argparse
from collections import Counter, defaultdict
import os
import hashlib
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
from decimal import Decimal, InvalidOperation
import re

MONTHS = {
    1: ("january", "jan", "januar", "jänner", "jaenner"),
    2: ("february", "feb", "februar"),
    3: ("march", "mar", "märz", "maerz", "mrz"),
    4: ("april", "apr"),
    5: ("may", "mai"),
    6: ("june", "jun", "juni"),
    7: ("july", "jul", "juli"),
    8: ("august", "aug"),
    9: ("september", "sep", "sept"),
    10: ("october", "oct", "oktober", "okt"),
    11: ("november", "nov"),
    12: ("december", "dec", "dezember", "dez"),
}
MONTH_NUMBER = {name: number for number, names in MONTHS.items() for name in names}
MONTH = "(" + "|".join(sorted((re.escape(name) for name in MONTH_NUMBER), key=len, reverse=True)) + r")\.?"

DATE_PATTERNS = (  # (pattern, order of the groups)
    (re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\.?\s*(?:of\s+)?{MONTH}\s+(\d{{4}})\b", re.I), "dMy"),
    (re.compile(rf"\b{MONTH}\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.I), "Mdy"),
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})\s?[./]\s?(\d{1,2})\s?[./]\s?(\d{4})\b"), "dmy"),
    (re.compile(rf"\b{MONTH}\s+(\d{{4}})\b", re.I), "My"),
)

UNITS = {  # spelling -> canonical unit; longest first when matching
    "months": "month", "month": "month", "monate": "month", "monaten": "month", "monat": "month",
    "litre": "litre", "litres": "litre", "liter": "litre", "litern": "litre", "liters": "litre", "mm": "mm",
    "%": "%", "prozent": "%", "percent": "%", "per cent": "%", "vom hundert": "%",
    "twh": "TWh", "gwh": "GWh", "mwh": "MWh", "kwh": "kWh", "wh": "Wh",
    "gw": "GW", "mw": "MW", "kw": "kW", "megawatt": "MW", "megawatts": "MW", "kilowatt": "kW", "kilowatts": "kW",
    "gigawatt": "GW", "gigawatts": "GW",
    "pj": "PJ", "tj": "TJ", "gj": "GJ", "mj": "MJ", "terajoule": "TJ", "terajoules": "TJ",
    "kv": "kV", "kilovolt": "kV",
    "t": "t", "tonnen": "t", "tonne": "t", "tonnes": "t", "tons": "t", "kg": "kg", "kilogramm": "kg",
    "kilograms": "kg", "kilogram": "kg", "mg": "mg", "µg": "µg", "μg": "µg",
    "km": "km", "kilometer": "km", "kilometres": "km", "kilometers": "km",
    "m²": "m2", "m2": "m2", "m³": "m3", "m3": "m3", "quadratmeter": "m2", "kubikmeter": "m3",
    "ppm": "ppm", "db": "dB", "db(a)": "dB",
    "eur": "EUR", "euro": "EUR", "€": "EUR",
}
UNIT = "(" + "|".join(sorted((re.escape(name) for name in UNITS), key=len, reverse=True)) + r")(?![\w²³])"
SPACED = r"\d{1,3}(?:[   ]\d{3})+(?![\d.,]\d)"
NUMBER = re.compile(rf"(?<![\d.,])(?:{SPACED}|\d+(?:[.,]\d+)*)(?![\d])")
UNIT_AFTER = re.compile(rf"\s?{UNIT}", re.I)
# A number right after one of these words points to a part of a text (Artikel 5, Absatz 1, Annex II, Nr. 451),
# it is not a fact such as a threshold or a deadline. Act numbers like 2008/98/EG are references too.
REFERENCE_WORDS = (
    r"Artikel|Article|Art\.|Absatz|Absätze|Abs\.|paragraph|Unterabsatz|Unterabsätze|subparagraph|Buchstabe|"
    r"Nummer|Nr\.|No\.?|Anhang|Anhänge|Annex|Teil|Part|Kapitel|Chapter|Abschnitt|Section|Satz|Sätze|Spalte|column|"
    r"Zeile|row|Tabelle|Table|Punkt|point|Ziffer|Unterpunkt|Anlage|Randnummer|Position|Kategorie|category|Klasse|class")
REFERENCE_BEFORE = re.compile(  # "Artikel 5", "Absätze 2 und", "Nummern 2 bis", "points (a) and"
    rf"(?:\b(?:{REFERENCE_WORDS})\w{{0,3}}\.?)\s*(?:\(\w+\)\s*)?"
    r"(?:(?:\d+[a-z]?|\(\w+\)|[ivx]+)\s*(?:,|und|and|oder|or|bis|to|sowie|-|–)\s*)*$", re.I)
SECTION_NUMBER = re.compile(r"^\d{1,2}(?:\.\d{1,2}){2,}$")   # 1.4.1.4 or 4.4.3: a section, not a value
ACT_NUMBER = re.compile(r"\b\d{1,4}/\d{2,4}(?:/(?:EU|EG|EWG|EC|EEC|Euratom|GASP|CFSP|JI|JHA))?\b", re.I)
CURRENCY_BEFORE = re.compile(r"(?:\bEUR|€|\bEuro)\s?$", re.I)   # EUR 1 500


def unit_of(name: str) -> str | None:
    """The canonical unit of a spelling. casefold() turns the micro sign µ into the Greek μ, so both are known."""
    return UNITS.get(name.casefold()) or UNITS.get(name.lower()) or UNITS.get(name)


def _decimal(text: str) -> str | None:
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    value = value.normalize()
    return format(value, "f") if value == value.to_integral() else str(value)


def values(token: str, *, lenient: bool = False) -> set[str]:
    """All values a number token can stand for. 1.500 -> {1500, 1.5}; 1.5 -> {1.5}; 1 000 -> {1000}."""
    token = token.strip()
    found = set()
    if re.fullmatch(SPACED, token):
        found.add(_decimal(re.sub(r"\D", "", token)))
        if lenient:  # in a table "300 800" can be two numbers
            found |= {_decimal(part) for part in re.split(r"[   ]", token)}
        return {value for value in found if value is not None}
    separators = re.findall(r"[.,]", token)
    groups = re.split(r"[.,]", token)
    if not separators:
        return {_decimal(token)} - {None}
    if len(set(separators)) == 2:  # 1.234,5 or 1,234.5: the last separator is the decimal one
        last = token[max(token.rfind("."), token.rfind(","))]
        whole, _, fraction = token.rpartition(last)
        found.add(_decimal(re.sub(r"\D", "", whole) + "." + fraction))
    elif len(separators) > 1:  # 1.000.000 or 1,000,000
        if all(len(group) == 3 for group in groups[1:]):
            found.add(_decimal("".join(groups)))
    else:
        whole, fraction = groups
        found.add(_decimal(f"{whole}.{fraction}"))  # 1.5 / 1,5
        if len(fraction) == 3:  # 1.500 can also be one thousand five hundred
            found.add(_decimal(whole + fraction))
    if lenient:
        found |= {_decimal(group) for group in groups}
    return {value for value in found if value is not None}


def dates(text: str) -> tuple[list[tuple[int, int, int | None]], str]:
    """The dates in a text as (year, month, day or None), and the text with the dates removed."""
    found = []

    def take(match, order):
        parts = dict(zip(order, match.groups()))
        month = parts["M"].casefold().rstrip(".") if "M" in parts else None
        number = MONTH_NUMBER.get(month) if month else int(parts["m"])
        day = int(parts["d"]) if "d" in parts else None
        if not number or not 1 <= number <= 12 or (day is not None and not 1 <= day <= 31):
            return match.group(0)
        found.append((int(parts["y"]), number, day))
        return " "

    for pattern, order in DATE_PATTERNS:
        text = pattern.sub(lambda match, order=order: take(match, order), text)
    return found, text


def numbers_with_units(text: str, *, lenient: bool = False, references: bool = True):
    """(token, its possible values, canonical unit or None) for every number in a text without dates.

    With references=False, numbers that only point to a part of a text or to another act are left out.
    """
    found = []
    if not references:
        text = ACT_NUMBER.sub(" ", text)
    for match in NUMBER.finditer(text):
        if not references and (SECTION_NUMBER.match(match.group(0))
                               or REFERENCE_BEFORE.search(text[max(0, match.start() - 40):match.start()])):
            continue
        unit = UNIT_AFTER.match(text, match.end())
        if unit:
            canonical = unit_of(unit.group(1))
        elif CURRENCY_BEFORE.search(text[max(0, match.start() - 6):match.start()]):
            canonical = "EUR"
        else:
            canonical = None
        found.append((match.group(0), values(match.group(0), lenient=lenient), canonical))
    return found


# Normalize a validation copy. The stored legal text and citation quotes retain their spelling.
_QWORDS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
           "fourteen fifteen sixteen seventeen eighteen nineteen").split()
_CARDINAL = {word: i for i, word in enumerate(_QWORDS)}
for _i, _ten in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), 2):
    _CARDINAL[_ten] = 10 * _i
    for _j, _one in enumerate(_QWORDS[1:10], 1):
        _CARDINAL[_ten + " " + _one] = 10 * _i + _j
_WORD_QUANTITY = re.compile(
    r"\b(" + "|".join(re.escape(w).replace(r"\ ", r"[ -]") for w in sorted(_CARDINAL, key=len, reverse=True))
    + r")(?=\s+(?:months?|litres?|liters?)\b)", re.I)
_CLOCK = re.compile(r"(?<![\w:])(?:"
                    r"(?P<h12>1[0-2]|0?[1-9])(?::(?P<m12>[0-5]\d))?\s*(?P<period>[ap])\.\s*m\.(?!\w)"
                    r"|(?P<h24>[01]?\d|2[0-3]):(?P<m24>[0-5]\d)(?![:\d]))", re.I)

def read_clocks(text):
    clocks = set()
    def take(match):
        if match['period']:
            hour = int(match['h12']) % 12 + (12 if match['period'].lower() == 'p' else 0)
            minute = int(match['m12'] or 0)
        else:
            hour, minute = int(match['h24']), int(match['m24'])
        clocks.add((hour, minute))
        return " "
    remainder = _CLOCK.sub(take, text)
    return clocks, remainder

def validation_copy(text):
    text = re.sub(r"\[P[1-9]\d*\]", " ", text)
    text = re.sub(r"\b1er\s+janvier\b", "1 January", text, flags=re.I)
    return _WORD_QUANTITY.sub(lambda m: str(_CARDINAL[m[1].lower().replace("-", " ")]), text)

class Source:
    """What a summary may use: the dates, numbers and number+unit pairs of the source text."""

    def __init__(self, text: str):
        self.clocks, remainder = read_clocks(validation_copy(str(text)))
        self.dates, rest = dates(remainder)
        self.values, self.with_unit, self.units = set(), set(), set()
        for _, possible, unit in numbers_with_units(rest, lenient=True):
            self.values |= possible
            if unit:
                self.with_unit |= {(value, unit) for value in possible}
        lowered = rest.casefold()
        self.units = {canonical for name, canonical in UNITS.items()
                      if re.search(rf"(?<![\w]){re.escape(name)}(?![\w²³])", lowered)}
        for year, month, day in self.dates:  # "2027" or "31" alone may be used as well
            self.values |= {str(year), str(month)} | ({str(day)} if day else set())
        self.values |= {_decimal(run) for run in re.findall(r"\d+", rest)} - {None}

    def has_date(self, date) -> bool:
        year, month, day = date
        return any(y == year and m == month and (day is None or d == day) for y, m, d in self.dates)


def number_problems(texts, source) -> list[str]:
    """Plain-language problems for the correction request. Empty when every number is in the source."""
    known = source if isinstance(source, Source) else Source(source)
    problems = []
    for text in texts:
        clocks, remainder = read_clocks(validation_copy(str(text)))
        for hour, minute in sorted(clocks - known.clocks):
            problems.append(f"Time {hour:02}:{minute:02} is absent from the source.")
        found, rest = dates(remainder)
        for date in found:
            if not known.has_date(date):
                year, month, day = date
                shown = f"{day}.{month}.{year}" if day else f"{month}/{year}"
                problems.append(f"The date {shown} does not occur in the text. Copy dates with day, month and year "
                                "exactly as they stand in the text.")
        for token, possible, unit in numbers_with_units(rest, references=False):
            if not possible & known.values:
                problems.append(f"The number {token} does not occur in the text. Keep the decimal point or comma "
                                "where it is (1.5 is not 15), or leave the number out.")
            elif unit and not any((value, unit) in known.with_unit for value in possible) and (unit in {"month", "litre", "mm"} or unit not in known.units):
                problems.append(f"'{token} {unit}' does not occur in the text with this unit. Write the number with "
                                "the unit the text uses.")
    return list(dict.fromkeys(problems))


# ------------------------------------------------------------------ language and empty statements

FUNCTION_WORDS = {
    "de": re.compile(r"\b(der|die|das|den|dem|des|und|oder|muss|müssen|für|mit|ist|sind|von|nicht|eine?|einen|wird|werden|bei|auf|zu|im|nach)\b", re.I),
    "en": re.compile(r"\b(the|and|or|must|shall|for|with|is|are|of|not|an?|will|be|at|on|to|in|by|which|that)\b", re.I),
}
NOTHING_STATED = re.compile(
    r"(no specific|not (?:specified|mentioned|stated|given|provided)|does not (?:specify|mention|state|provide)|"
    r"there is no (?:specific |explicit )?(?:deadline|time limit|date)|"
    r"keine (?:konkrete|spezifische|bestimmte|ausdrückliche|explizite)?\s*(?:Frist|Angabe|Zeitangabe|Termin)|"
    r"nicht (?:genannt|angegeben|festgelegt|erwähnt|spezifiziert|bestimmt)|wird nicht (?:genannt|angegeben|erwähnt))",
    re.I)


def wrong_language(texts, language: str) -> bool:
    """True when the texts are clearly in the other language (more of its function words)."""
    text = " ".join(texts)
    other = "en" if language == "de" else "de"
    own, foreign = len(FUNCTION_WORDS[language].findall(text)), len(FUNCTION_WORDS[other].findall(text))
    return foreign >= 4 and foreign > 1.5 * own


def states_nothing(text: str) -> bool:
    """True for a sentence whose point is that the text does not say something."""
    return bool(NOTHING_STATED.search(text))

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


class RejectedAnswer(Unclassified):
    def __init__(self, sent, attempts):
        super().__init__("Correction failed: " + attempts[-1]["reason"])
        self.evidence = sent
        self.attempts = attempts


def ask(client, system, sent, task, schema, name, check, log, max_tokens):
    """One question to Qwen with at most one correction request. `check` validates the parsed reply."""
    evidence = json.dumps(sent, ensure_ascii=False)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": f"{evidence}\n\n{task}"}]
    attempts = []
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
            attempts.append({"answer": candidate, "reason": str(exc)})
            if attempt:
                raise RejectedAnswer(sent, attempts) from exc
            if candidate is not None:
                messages.append({"role": "assistant", "content": json.dumps(candidate, ensure_ascii=False)})
            messages.append({"role": "user", "content": (
                f"Revise the answer using the original passages. Validation feedback: {exc} "
                "Check the headline, overview and each bullet. Copy supported quantities exactly. "
                "If a claim depends on an unsupported quantity, remove the claim. Preserve conditions "
                "and exceptions. Do not invent a replacement value. Return the full JSON object.")})


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
    known = Source(source)
    for field, text in [("headline", headline), ("overview", overview),
                        *((f"bullet {i}", b["text"]) for i, b in enumerate(result, 1))]:
        problems = number_problems([text], known)
        if problems:
            raise Unclassified(f"{field}: " + " ".join(problems[:3]))
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
        ordered = list(passages)
        # Join contiguous cited passages only. Isolated words from distant passages
        # must not form a date accidentally.
        cited = " ".join(passages[m] if m in marks else " [uncited] " for m in ordered)
        return number_problems([bullet["text"]], locator + " " + cited)

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
        locator = str(sent["target"].get("locator", ""))
        source = str(sent["target"]["text"])
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


DRAFT_HASH = {"31982R1334": "009abfa9bc87f327214d334ba8353d4950d8624170ca3337efdb5a491d089242", '31980D0372': '181cb188f9fd5dc47b08fa028b23fed90689426c740fdf2cf5484df8c118a3dc', '31985D0613': '28791ebd98861a67c7cf2e2fe68c2c57796ff10c49ef4301f100cdb6ae9361b5'}
# Two individually checked German drafts. Use them only against the same diagnostic
# passage boundaries and the same local provision. This avoids accepting an incorrect
# institution or slipping annex requirements into an article summary.
DIAMETER_SOURCE = "https://eur-lex.europa.eu/legal-content/ro/ALL/?uri=CELEX%3A31982R1334"
DIAMETER_QUOTE = "con diametro esterno di 168,3 millimetri o meno"

SPECIAL = {
    ("31982R1334", "Article 1"): (
        "Annahme der Verpflichtung von Metalimportexport",
        "Die Kommission nimmt die von Metalimportexport angebotene Verpflichtung im Antidumpingverfahren an. Sie betrifft bestimmte geschweißte, nicht verzinkte Rohre aus Eisen oder Stahl mit kreisförmigem Querschnitt und Ursprung in Rumänien.",
        [("CONDITION", "Erfasst sind Rohre mit einem Außendurchmesser von höchstens 168,3 mm.", ["P1", "P2"]),
         ("OTHER", "Der Artikel nennt Gewinderohre oder zum Gewindeschneiden geeignete Rohre sowie weitere Rohre der genannten Zolltarifposition.", ["P2"])]),
    ("31980D0372", "Article 2"): (
        "Überprüfung der Maßnahmen und Aufgaben von Mitgliedstaaten und Rat",
        "Die Maßnahmen werden im ersten Halbjahr 1980 anhand der verfügbaren wissenschaftlichen und wirtschaftlichen Daten überprüft. Die Mitgliedstaaten liefern der Kommission Studien- und Forschungsergebnisse. Der Rat beschließt auf Vorschlag der Kommission die erforderlichen weiteren Maßnahmen.",
        [("DUTY", "Die Mitgliedstaaten übermitteln der Kommission die ihnen vorliegenden Studien- und Forschungsergebnisse unter Wahrung der Geschäftsgeheimnisse.", ["P1", "P2"]),
         ("DEADLINE", "Der Rat beschließt auf Vorschlag der Kommission so bald wie möglich, spätestens am 30. Juni 1981, die nach der Überprüfung erforderlichen weiteren Maßnahmen.", ["P2", "P3"])]),
    ("31985D0613", "Article 4 [source path article/4~duplicate-2]"): (
        "Umsetzung der Programme und Maßnahmen durch die Vertragsparteien",
        "Die Vertragsparteien setzen die Programme und Maßnahmen ab dem 1. Januar 1986 um. Sie informieren die Kommission über die innerstaatlichen Rechtsvorschriften, die sie zu deren Umsetzung erlassen haben.",
        [("DEADLINE", "Die Vertragsparteien setzen die Programme und Maßnahmen ab dem 1. Januar 1986 um.", ["P1"]),
         ("DUTY", "Die Vertragsparteien unterrichten die Kommission über ihre innerstaatlichen Umsetzungsvorschriften.", ["P1", "P2"])])
}

def checked_draft(key, row, provision):
    raw = row["evidence"]["target"]["text"]
    if key[0] == "31985D0613":
        raw, boundary, _ = raw.partition(" No L 375/ 30. 12. 85 00 ANNEXE I")
        if not boundary:
            raise ValueError("Annex boundary differs from the checked source.")
    if hashlib.sha256(raw.encode()).hexdigest() != DRAFT_HASH[key[0]]:
        raise ValueError("Diagnostic source differs from the checked passage.")
    pieces = re.split(r"\[(P\d+)\]\s*", raw)
    passages = {k: v.strip() for k, v in zip(pieces[1::2], pieces[2::2])}
    body = squash(" ".join(passages.values()))
    # Some source packs deliver the locator separately from the body.
    substantive = re.sub(r"^Article \d+\s+", "", body)
    if substantive not in squash(" ".join(target_text(provision))):
        raise ValueError("Local provision differs from the diagnostic. Review current source.")
    headline, overview, bullets = SPECIAL[key]
    candidate = dict(headline=headline, overview=overview,
                     bullets=[dict(kind=k, text=t, passages=marks) for k,t,marks in bullets])
    validation_passages = passages.copy()
    if key[0] == "31982R1334":
        validation_passages["P2"] = passages["P2"].replace("168;3 mm", "168,3 mm", 1)
    summary = validate_provision(candidate, validation_passages, " ".join(validation_passages.values()), final=False)
    for bullet in summary["bullets"]:
        bullet.update(part="1", quotes=[passages[m][:400] for m in bullet["passages"]])
    supplementary = {}
    if key[0] == "31982R1334":
        supplementary = {"official_source": DIAMETER_SOURCE, "source_language": "it",
                         "official_excerpt": DIAMETER_QUOTE, "source_reading": "168;3 mm",
                         "verified_reading": "168,3 mm"}
    return summary, dict(**supplementary, method="source-checked German draft", parts_total=1, parts=["1"],
                         not_read=[], requests=[], included_source=raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--failed-report", type=Path, required=True, help="Select still_failed rows from this retry JSONL.")
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
    if args.failed_report:
        with args.failed_report.open(encoding="utf-8-sig") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        diagnostic = {(row["celex"], row["target"]): row for row in rows if row["status"] == "still_failed"}
        keys &= {(row["celex"], row["target"]) for row in rows if row["status"] == "still_failed"}
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
    log = out / f"provision_repair_{stamp}.jsonl"
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
                key = (celex, target)
                row = diagnostic[key]
                if not row.get("evidence") or not row.get("attempts"):
                    raise ValueError("Expected a diagnostic with source passages and rejected answers.")
                if key in SPECIAL:
                    if OUTPUT_LANGUAGE != "de":
                        raise ValueError("Source-checked draft is German; review this language separately.")
                    summary, audit = checked_draft(key, row, pack["provision"])
                else:
                    prompt = systems["provision"] + (
                        "\nPreserve the source's actor, obligation or permission, conditions and deadline. "
                        "Do not assign an unnamed employer, infer funding, or equate first and last calls "
                        "with exactly two calls. Use German zollfrei for duty-free. "
                        "Quote quantities faithfully and keep citations for both halves of a split date.")
                    summary, audit = summarise_provision(client, prompt, evidence, systems["merge"])
                if summary.get("text_missing"):
                    raise ValueError("Insufficient local text for this retry.")
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
                if isinstance(exc, RejectedAnswer):
                    event.update(evidence=exc.evidence, attempts=exc.attempts)
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
