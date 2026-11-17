"""Checks that a summary does not change numbers, dates or units of the legal text.

Used by summarize_eurlex_provisions.py. A summary may write a number in another
notation (1.000 / 1,000 / 1 000, 1.5 / 1,5, 31.12.2027 / 31 December 2027), but
never another value:

- a date in the summary must be the same day, month and year as a date in the source;
  a date without day (Dezember 2027) must match month and year;
- 1.5 and 15 are different numbers: separators are read as decimal or thousands
  separators, never simply dropped;
- a number with a unit (MW, kWh, t, %, ...) must stand with the same unit in the source.

    problems = number_problems(texts, source)   # [] when everything is in the source
"""

from __future__ import annotations

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
    (re.compile(rf"\b(\d{{1,2}})\.?\s*(?:of\s+)?{MONTH}\s+(\d{{4}})\b", re.I), "dMy"),
    (re.compile(rf"\b{MONTH}\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.I), "Mdy"),
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})\s?[./]\s?(\d{1,2})\s?[./]\s?(\d{4})\b"), "dmy"),
    (re.compile(rf"\b{MONTH}\s+(\d{{4}})\b", re.I), "My"),
)

UNITS = {  # spelling -> canonical unit; longest first when matching
    "%": "%", "prozent": "%", "percent": "%", "per cent": "%", "vom hundert": "%",
    "twh": "TWh", "gwh": "GWh", "mwh": "MWh", "kwh": "kWh", "wh": "Wh",
    "gw": "GW", "mw": "MW", "kw": "kW", "megawatt": "MW", "megawatts": "MW", "kilowatt": "kW", "kilowatts": "kW",
    "gigawatt": "GW", "gigawatts": "GW",
    "pj": "PJ", "tj": "TJ", "gj": "GJ", "mj": "MJ", "terajoule": "TJ", "terajoules": "TJ",
    "kv": "kV", "kilovolt": "kV",
    "t": "t", "tonnen": "t", "tonne": "t", "tonnes": "t", "tons": "t", "kg": "kg", "kilogramm": "kg",
    "kilograms": "kg", "kilogram": "kg", "mg": "mg", "µg": "µg",
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
REFERENCE_BEFORE = re.compile(
    r"(?:\bArtikels?|\bArticles?|\bArt\.|\bAbsatz|\bAbs\.|\bparagraphs?|\bUnterabsatz|\bsubparagraphs?|"
    r"\bBuchstaben?|\bNummer|\bNr\.|\bNo\.?|\bAnhang|\bAnnex(?:es)?|\bTeil|\bPart|\bKapitel|\bChapter|"
    r"\bAbschnitt|\bSection|\bSatz|\bSpalte|\bcolumn|\bZeile|\brow|\bTabelle|\bTable|\bPunkt|\bpoints?|"
    r"\bZiffer|\bUnterpunkt|\bAnlage|\bRandnummer)\s*(?:\(\w+\)\s*)?$", re.I)
ACT_NUMBER = re.compile(r"\b\d{1,4}/\d{2,4}(?:/(?:EU|EG|EWG|EC|EEC|Euratom|GASP|CFSP|JI|JHA))?\b", re.I)
CURRENCY_BEFORE = re.compile(r"(?:\bEUR|€|\bEuro)\s?$", re.I)   # EUR 1 500


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
        if not references and REFERENCE_BEFORE.search(text[max(0, match.start() - 25):match.start()]):
            continue
        unit = UNIT_AFTER.match(text, match.end())
        if unit:
            canonical = UNITS[unit.group(1).casefold()]
        elif CURRENCY_BEFORE.search(text[max(0, match.start() - 6):match.start()]):
            canonical = "EUR"
        else:
            canonical = None
        found.append((match.group(0), values(match.group(0), lenient=lenient), canonical))
    return found


class Source:
    """What a summary may use: the dates, numbers and number+unit pairs of the source text."""

    def __init__(self, text: str):
        self.dates, rest = dates(str(text))
        self.values, self.with_unit = set(), set()
        for _, possible, unit in numbers_with_units(rest, lenient=True):
            self.values |= possible
            if unit:
                self.with_unit |= {(value, unit) for value in possible}
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
        found, rest = dates(str(text))
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
            elif unit and not any((value, unit) in known.with_unit for value in possible):
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
