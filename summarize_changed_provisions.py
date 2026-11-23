"""Summary version 2 for the relevant provisions that EUR-Lex changed.

Input:
- the export of the backend (python -m app.cli.export_changed_texts), folder
  reports/changed-texts/<time>/: per relevant act each changed article or annex with its text in
  force today and its changes (latest from 2015 on and upcoming with old and new wording,
  earlier ones as one line);
- the classification (runs/eurlex_classification_v5): which provisions are relevant.

For every relevant article or annex that was changed (one unit per article or annex):
1. Summary version 2: written like version 1 (same prompt, same format: headline, paragraph,
   up to 4 key points, "Weitere Punkte"), but from today's text. A long text is read in parts
   as in version 1; the parts that hold the latest change are read first.
2. "Was hat sich geändert": fact lines without AI (date, place, what happened, amending act)
   and one or two sentences by Qwen, written only from the stored old and new wording of the
   latest change (no request when there is no wording).
3. Upcoming changes: one line each ("Ab 01.01.2027: ... wird ersetzt") and, when their wording
   is stored, one Qwen sentence on what will be different, per day the changes apply; never
   mixed into the summary.

Not asked again: a deleted article ("Gestrichen am ..."), an article without a newer text
(today's text is the original act: version 1 stays), and tables: a relevant table of a changed
annex keeps its version-1 summary and gets the annex's "Was hat sich geändert".

Version 1 is not changed. Output folder (default runs/eurlex_summaries_v2_changes):
    units/<CELEX>/<article or annex>.json   one record per changed article or annex
    changed_provisions.json                 all records, for the database import and the UI
    review.csv                              one row per record, to check by hand
    preview/index.html                      the act pages as the UI should show them
    summary.json                            counts

Ctrl+C stops; the same command continues. When Qwen cannot be reached it waits as the other
steps do.

    python summarize_changed_provisions.py --changed-dir <backend>/reports/changed-texts/<time> --count-only
    python summarize_changed_provisions.py --changed-dir <backend>/reports/changed-texts/<time> --sample 20
    python summarize_changed_provisions.py --changed-dir <backend>/reports/changed-texts/<time>
"""

import argparse
from collections import Counter, defaultdict
import csv
import difflib
from datetime import date
import html
import json
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import summarize_eurlex_provisions as base  # noqa: E402
from classify_eurlex_provisions import (  # noqa: E402
    KEPT, MAX_WORKERS, ConnectionLost, Unclassified, reachable, read_json, run_until_done, safe_name,
    squash, wait_until_reachable, write_json)
from eurlex_provision.simple import TARGET_CHARS, split_target, target_text  # noqa: E402
from eurlex_summary_checks import FUNCTION_WORDS  # noqa: E402
from lr_ai.qwen_client import QwenClient  # noqa: E402

CHANGE_PROMPT = ROOT / "prompts/eurlex_change_summary.md"
DEFAULT_CLASSIFICATION = ROOT / "runs/eurlex_classification_v5"
DEFAULT_OUTPUT = ROOT / "runs/eurlex_summaries_v2_changes"
VERSION = "changes-v6"
CHANGE_TASK = "TASK: Write what changed. Return the JSON object described in the instructions."
CHANGE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["sentence"],
                 "properties": {"sentence": {"type": "string"}}}
MAX_PAIRS = 6               # changed places of one day sent to Qwen
MAX_WORDING = 3000          # characters of old and of new wording per place
MAX_FACT_LINES = 8          # fact lines shown per article; the rest is counted
ROOT_LOCATOR = re.compile(r"^(Article|Annex)\b\s*([0-9]+[a-z]*|[IVXLC]+[a-z]*)?\b", re.I)

# ------------------------------------------------------------------ words for the fact lines

OPERATION_DE = {"REPLACE": "ersetzt", "MODIFY": "geändert", "ADD": "eingefügt", "INSERT": "eingefügt",
                "DELETE": "gestrichen", "REPEAL": "aufgehoben", "OTHER": "geändert"}
RELATION_DE = {"SUSPENDS": "ausgesetzt", "PARTIALLY_SUSPENDS": "teilweise ausgesetzt",
               "DEROGATES": "Ausnahme vorgesehen", "DEFERS_APPLICATION": "Anwendung verschoben",
               "RENDERS_OBSOLETE": "gegenstandslos", "REESTABLISHES": "wieder in Kraft gesetzt",
               "INCORPORATES": "übernommen"}
TOKEN_DE = {"paragraph": "Absatz", "subpoint": "Ziffer", "part": "Teil", "table": "Tabelle",
            "appendix": "Anlage", "title": "Überschrift", "unnumbered paragraph": "Unterabsatz"}
TOKEN = re.compile(r"\b(unnumbered paragraph|paragraph|subpoint|point|part|table|appendix|title)\b\s*(\S*)")


def roman(number):
    values = [(10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
    out = ""
    for value, letters in values:
        while number >= value:
            out, number = out + letters, number - value
    return out


def german_place(place):
    """'Article 6 paragraph 5 point b' -> 'Artikel 6 Absatz 5 Buchstabe b'."""
    if not place:
        return ""
    head = re.sub(r"^Article\b", "Artikel", re.sub(r"^Annex\b", "Anhang", place.strip()))
    head = re.sub(r"^Anhang SOLE\b", "Anhang", head)   # the only annex of an act has no number
    match = TOKEN.search(head)
    start, rest = (head[:match.start()].strip(), head[match.start():]) if match else (head, "")
    words = []
    for kind, number in TOKEN.findall(rest):
        if kind == "point":
            word = "Buchstabe" if re.fullmatch(r"[a-z]{1,3}", number) else "Nummer"
        else:
            word = TOKEN_DE[kind]
        words.append(f"{word} {number}".strip())
    return " ".join([start, *words]).strip()


def act_name(celex):
    """'32024R1257' -> 'Verordnung 2024/1257'; other CELEX numbers stay as they are."""
    kind = base.act_type(celex, "de")
    match = base.CELEX_PARTS.fullmatch(celex or "")
    if not kind or not match:
        return f"Rechtsakt {celex}"
    year, number = int(match.group(1)), str(int(match.group(3)))
    if match.group(2) == "R" and year < 2015:
        return f"{kind} Nr. {number}/{year}"
    return f"{kind} {year}/{number}"


def day_de(text):
    try:
        day = date.fromisoformat(text)
    except (TypeError, ValueError):
        return text or "ohne Datum"
    return f"{day:%d.%m.%Y}"


def version_de(label):
    """'version 2026-09-23' -> 'Fassung vom 23.09.2026'; 'original text' -> 'ursprünglicher Text'."""
    if (label or "").startswith("version "):
        return f"Fassung vom {day_de(label.split()[-1])}"
    return "ursprünglicher Text" if label == "original text" else (label or "")


def what_happened(line):
    if line.get("status") == "NO_WORDING_NEEDED" or line["operation"] not in OPERATION_DE:
        for relation, word in RELATION_DE.items():
            if (line.get("relation") or "").startswith(relation):
                return word
        if (line.get("relation") or "").startswith("EXTENDS"):
            return "verlängert"
    return OPERATION_DE.get(line["operation"], "geändert")


def fact_line(line):
    """'07.07.2024 – Absatz 3 ersetzt durch Verordnung 2024/1257' (no AI)."""
    place = german_place(line.get("place")) or line.get("where") or "die Vorschrift"
    return f"{day_de(line['date'])} – {place} {what_happened(line)} durch {act_name(line['changing_act'])}"


def upcoming_line(line):
    place = german_place(line.get("place")) or line.get("where") or "die Vorschrift"
    return f"Ab {day_de(line['date'])}: {place} wird {what_happened(line)} ({act_name(line['changing_act'])})"


# ------------------------------------------------------------------ which provisions

def root_of_locator(locator):
    """('annex', 'i') of a relevant provision such as 'Annex I [source path annex/i]'; None for others."""
    text = base.plain_locator(locator)
    match = ROOT_LOCATOR.match(text)
    if not match:
        return None
    if match.group(2) is None and re.match(r"\s*[^\s,\[]", text[match.end():]):
        return None  # a named annex ("Annex B-03 ..."): not matched by number
    kind, number = match.group(1).lower(), (match.group(2) or "sole").lower()
    return kind, number


def find_root(roots, kind, number):
    """The key of the changed article or annex in the export, also when one side writes the annex
    number in Roman and the other in Arabic numbers, or the only annex without a number."""
    keys = [f"{kind}/{number}"]
    if number.isdigit():
        keys.append(f"{kind}/{roman(int(number))}")
    elif re.fullmatch(r"[ivxlc]+", number):
        keys.append(f"{kind}/{base.roman_value(number.upper())}")
    for key in keys:
        if key in roots:
            return key
    annexes = [key for key in roots if key.startswith("annex/")]
    if kind == "annex" and number == "sole" and len(annexes) == 1:
        return annexes[0]
    return None


def merged(folders):
    """The provision classifications of several folders, by act; the first folder wins for a provision
    that is in more than one."""
    result = {}
    for folder in folders:
        path = Path(folder) / "results.json"
        if folder != folders[0] and not path.is_file():
            continue  # an extra classification that was not written (nothing relevant was found)
        for celex, provisions in read_json(path).items():
            for locator, value in provisions.items():
                value = dict(value, inserted_later=True) if folder != folders[0] else value
                result.setdefault(celex, {}).setdefault(locator, value)
    return result


def plan(changed_dir, classified, levels, celexes=()):
    """[unit] for every changed article or annex that holds a relevant provision, and the
    counts of what was matched."""
    index = read_json(changed_dir / "index.json")
    units, facts = [], Counter()
    for celex in sorted(index):
        relevant = {loc: v for loc, v in classified.get(celex, {}).items() if v.get("level") in levels}
        if not relevant or (celexes and celex not in celexes):
            continue
        record = read_json(changed_dir / "acts" / f"{celex}.json")
        roots = record["roots"]
        matched = defaultdict(lambda: {"provisions": [], "tables": []})
        for locator, result in sorted(relevant.items()):
            parsed = root_of_locator(locator)
            key = find_root(roots, *parsed) if parsed else None
            if key is None:
                facts["relevant provisions not changed"] += 1
                continue
            group = "tables" if base.TABLE_PIECE.match(base.plain_locator(locator)) else "provisions"
            matched[key][group].append({"locator": locator, "level": result.get("level"),
                                        "inserted_later": bool(result.get("inserted_later")),
                                        "departments": result.get("departments", []),
                                        "domain": result.get("primary_domain")})
            facts[f"relevant {group} changed"] += 1
        for key, found in sorted(matched.items()):
            root = roots[key]
            units.append({"celex": celex, "title": record.get("title", ""), "act_level": record.get("level"),
                          "today_version": record.get("today_version"),
                          "today_version_celex": record.get("today_version_celex"), "key": key,
                          "root": root, **found})
    return units, facts


def pick_sample(units, count):
    """A fixed mix for a test run, newest acts first: articles with old/new wording of the latest change,
    with upcoming changes, articles inserted later, and the others."""
    def has(unit, role):
        return any(line["role"] == role and (line.get("old_text") or line.get("new_text"))
                   for line in unit["root"]["changes"])

    groups = [
        [u for u in units if any(p.get("inserted_later") for p in u["provisions"])],
        [u for u in units if has(u, "upcoming")],
        [u for u in units if has(u, "latest")],
        [u for u in units if unit_kind(u) == "summary"],
        list(units),
    ]
    chosen, seen = [], set()
    queues = [sorted(group, key=lambda u: (u["celex"][1:5], u["celex"]), reverse=True) for group in groups]
    while len(chosen) < count and any(queues):
        for queue in queues:
            while queue and id(queue[0]) in seen:
                queue.pop(0)
            if queue and len(chosen) < count:
                unit = queue.pop(0)
                seen.add(id(unit))
                chosen.append(unit)
    return chosen


def foreign(text):
    """True when a German text is clearly English (English function words, no German ones)."""
    return (len(FUNCTION_WORDS["en"].findall(text or "")) >= 2
            and not FUNCTION_WORDS["de"].findall(text or ""))


def unit_kind(unit):
    """summary: version 2 is written; deleted; same_text: today's text is the original act (no
    newer text, version 1 stays); no_text: the article could not be cut out of today's text;
    tables: only tables of the annex are relevant."""
    root = unit["root"]
    if root.get("deleted"):
        return "deleted"
    if not unit["provisions"]:
        return "tables"
    if not root.get("today"):
        return "no_text"
    if (root["today"].get("version_kind") or "") != "CONSOLIDATED":
        return "same_text"
    return "summary"


# ------------------------------------------------------------------ the requests

def text_node(unit):
    today = unit["root"]["today"]
    return {"locator": unit["root"]["name"], "heading": today.get("heading") or "", "text": today["text"]}


def saved_pack(unit, node):
    """The shape summarise_provision() reads: the act, today's text, and which parts to read
    first (those that hold the wording of the latest change)."""
    pack = {"document": {"celex": unit["celex"], "title": unit["title"]}, "provision": node,
            "essential_context": [], "direct_references": [], "footnotes": [], "unavailable_references": []}
    parts = split_target(node, TARGET_CHARS)
    saved = {"evidence_pack": pack}
    if len(parts) > 1:
        snippets = [squash(line.get("new_text") or "")[:80] for line in unit["root"]["changes"]
                    if line["role"] in ("latest", "upcoming") and line.get("new_text")]
        texts = [squash(" ".join(target_text(part))) for part in parts]
        first = [index for index, text in enumerate(texts) if any(s and s in text for s in snippets)]
        chosen = (first + [index for index in range(len(parts)) if index not in first])[:base.MAX_PARTS]
        saved["model_attempt"] = {"parts": [
            {"part": str(index + 1), "decision": {"level": "DIRECT" if index in chosen else "IGNORE"}}
            for index in range(len(parts))]}
    return saved


def latest_lines(unit):
    return [line for line in unit["root"]["changes"] if line["role"] == "latest"]


def upcoming_days(unit):
    """{day: [lines]} of the upcoming changes, earliest day first."""
    days = {}
    for line in sorted((line for line in unit["root"]["changes"] if line["role"] == "upcoming"),
                       key=lambda line: line["date"] or ""):
        days.setdefault(line["date"], []).append(line)
    return days


SENTENCE_END = re.compile(r"(?<=[.;:])\s+|\s*\n+\s*")


CONTEXT_WORDS = 15
MAX_PAIR = 1500             # characters of one side of one differing passage


def _words(parts):
    return " ".join(parts).split()


def _changed(old_parts, new_parts, context, join_gap=0):
    """[{before, old, new, after}]: each differing passage of two lists of parts (sentences or words). old and
    new hold only what differs; before and after are up to CONTEXT_WORDS unchanged words around it (the same
    on both sides), so that the reader sees where the passage is, for example the row name of a table.
    Differences with at most `join_gap` equal parts between them are joined into one passage."""
    groups = []
    matcher = difflib.SequenceMatcher(None, old_parts, new_parts, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if groups and i1 - groups[-1][1] <= join_gap:
            groups[-1] = (groups[-1][0], i2, groups[-1][2], j2)
        else:
            groups.append((i1, i2, j1, j2))
    pairs = []
    for index, (i1, i2, j1, j2) in enumerate(groups):
        start = groups[index - 1][1] if index else 0
        end = groups[index + 1][0] if index + 1 < len(groups) else len(old_parts)
        before = _words(old_parts[max(start, i1 - context):i1])[-CONTEXT_WORDS:]
        after = _words(old_parts[i2:min(end, i2 + context)])[:CONTEXT_WORDS]
        pair = {"before": " ".join(before), "old": " ".join(old_parts[i1:i2]), "new": " ".join(new_parts[j1:j2]),
                "after": " ".join(after)}
        pairs.append({key: value for key, value in pair.items() if value})
    return pairs


DASHES = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
                        "\u2212": "-", "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})


def plain(text):
    """The same characters for the same thing (dashes, quotes, a middle dot), so that only real changes differ."""
    return unicodedata.normalize("NFKC", text or "").translate(DASHES)


def differences(old, new):
    """[{before, old, new, after}]: only the passages of two long wordings that differ, in their order. Used
    when a wording is longer than MAX_WORDING (a replaced annex or table), so that Qwen compares what changed,
    not the start. Sentence by sentence first; for tables and lists (text without sentence ends) word by word.
    Each passage has some unchanged words around it."""
    def size(pairs):
        return sum(len(pair.get("old", "")) + len(pair.get("new", "")) for pair in pairs)

    old, new = plain(old), plain(new)
    pairs = _changed([p for p in SENTENCE_END.split(old or "") if p.strip()],
                     [p for p in SENTENCE_END.split(new or "") if p.strip()], 2)
    if size(pairs) > MAX_WORDING // 2:
        by_word = _changed((old or "").split(), (new or "").split(), CONTEXT_WORDS, join_gap=3)
        if size(by_word) < size(pairs):
            pairs = by_word
    return pairs


HEADING = r"(?:^|(?<=[.;:)\]] ))Article (\d+[a-z]*) (?=[A-Z])"


def own_article(text, name):
    """The wording of one article when the stored wording holds several inserted articles ("Article 10a ...
    Article 10b ..."): cut at the heading of the next article. Other text stays as it is."""
    match = re.fullmatch(r"Article (\d+[a-z]*)", name or "")
    if not match or not (text or "").startswith(f"Article {match.group(1)} "):
        return text
    for heading in re.finditer(HEADING, text):
        if heading.start() and heading.group(1) != match.group(1):
            return text[:heading.start()].rstrip()
    return text


ROOT_HEADING = re.compile(r"\s*(?:ANNEX|Annex|ARTICLE|Article|APPENDIX|Appendix)\b")


def whole_root(text, place, root_name):
    """True when the only wording of a deletion or an addition is the whole article or annex (a long text that
    starts with its heading) and not the deeper place itself, for example the whole list of standards for
    "Annex I point 198". Such a text does not show what was deleted or added."""
    return (bool(text) and (place or "") != root_name and len(text) > MAX_WORDING
            and bool(ROOT_HEADING.match(text)))


def change_input(unit, lines=None, applies_from=None):
    """What Qwen sees for the 'Was hat sich geändert' sentence (the latest change), or for an upcoming
    day's sentence (lines and applies_from given). None when there is no wording."""
    pairs, more = [], 0
    for line in latest_lines(unit) if lines is None else lines:
        old, new = line.get("old_text"), line.get("new_text")
        place, name = line.get("place") or "", unit["root"]["name"]
        if new and place == name:
            new = own_article(new, name)
        if old and not new and whole_root(old, place, name):
            old = None
        if new and not old and whole_root(new, place, name):
            new = None
        if not (old or new) or len(pairs) >= MAX_PAIRS:   # counted: a place without its own wording, or more
            more += 1
            continue
        item = {"place": line.get("place") or line.get("where"),
                "type": {"REPLACE": "replaced", "ADD": "added", "INSERT": "added", "DELETE": "deleted",
                         "REPEAL": "deleted"}.get(line["operation"], "changed")}
        if old and new and not line.get("old_is_context") and max(len(old), len(new)) > MAX_WORDING:
            shown, used, left = [], 0, 0
            for found in differences(old, new):
                pair = {key: value[:MAX_PAIR] for key, value in found.items()}
                length = sum(map(len, pair.values()))
                if shown and used + length > 2 * MAX_WORDING:
                    left += 1
                    continue
                shown.append(pair)
                used += length
            item["differences"] = shown
            if left:
                item["more_differences"] = left
        else:
            if old and not (line.get("old_is_context") and len(old) > MAX_WORDING):
                item["old_wording"] = old[:MAX_WORDING]
                if line.get("old_is_context"):
                    item["old_is_context"] = True
            if new:
                item["new_wording"] = new[:MAX_WORDING]
        if "old_wording" in item or "new_wording" in item or item.get("differences"):
            pairs.append(item)
        else:
            more += 1
    if not pairs:
        return None
    sent = {"act": {"celex": unit["celex"], "title": unit["title"]}, "provision": unit["root"]["name"],
            "changes": pairs}
    if applies_from:
        sent["applies_from"] = applies_from
    if more:
        sent["more_changes"] = more
    return sent


PAST = re.compile(r"\b(?:wurden?|wurde|hat sich|haben sich)\b", re.I)
GERMAN_PLACES = [(re.compile(r"\bAnnexes\b"), "Anhänge"), (re.compile(r"\bAnnex\b"), "Anhang"),
                 (re.compile(r"\bArticles\b"), "Artikel"), (re.compile(r"\bArticle\b"), "Artikel"),
                 (re.compile(r"\bAppendix\b"), "Anlage"), (re.compile(r"\bAppendices\b"), "Anlagen"),
                 (re.compile(r"\b(?:[Pp]oints?|Punkte?)\s+(?=\d)"), "Nummer "),
                 (re.compile(r"\b[Pp]aragraphs?\s+(?=\d)"), "Absatz ")]


def german_places(text):
    """English words for parts of an act ("Annex XV", "Article 6") in German; quoted text stays as it is."""
    pieces = re.split(r"([‘'„\"“][^‘'„\"“”’]*[’'“\"”])", text)
    for index in range(0, len(pieces), 2):
        for pattern, word in GERMAN_PLACES:
            pieces[index] = pattern.sub(word, pieces[index])
    return "".join(pieces)


def annex_or_appendix(text, source):
    """Anhang (Annex) and Anlage (Appendix) as in the English wording: "Anlagen XIV, XVI" becomes "Anhänge
    XIV, XVI" when the wording has no Appendix XIV, and "Anhang 1" becomes "Anlage 1" when the wording has
    Appendix 1 but no Annex 1."""
    def annex(match):
        if re.search(rf"\bAppendix {match.group(2)}\b", source):
            return match.group(0)
        return f"{'Anhänge' if match.group(1) else 'Anhang'} {match.group(2)}"

    def appendix(match):
        number = match.group(1)
        if re.search(rf"\bAppendix {number}\b", source) and not re.search(rf"\bAnnex {number}\b", source):
            return f"Anlage {number}"
        return match.group(0)

    text = re.sub(r"\bAnlage(n)? ([IVXLC]+)\b", annex, text)
    return re.sub(r"\bAnhang (\d+)\b", appendix, text)


MONTHS_DE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
             "November", "Dezember"]


def names_day(text, day):
    """True when the text writes the given ISO day (13.07.2027, 13. Juli 2027, 2027-07-13)."""
    try:
        value = date.fromisoformat(day or "")
    except ValueError:
        return False
    month = MONTHS_DE[value.month - 1]
    forms = [f"{value:%d.%m.%Y}", f"{value.day}.{value.month}.{value.year}", value.isoformat(),
             f"{value.day}. {month} {value.year}", f"{value:%d}. {month} {value.year}"]
    return any(form in text for form in forms)


def changed_only(sent):
    """The input without the unchanged words around each difference (before, after); None when no
    differences are sent."""
    if not any(item.get("differences") for item in sent.get("changes", [])):
        return None
    copy = json.loads(json.dumps(sent))
    for item in copy["changes"]:
        for pair in item.get("differences", []):
            pair.pop("before", None)
            pair.pop("after", None)
    return json.dumps(copy, ensure_ascii=False)


VALUE_IN_MARK = re.compile(r"^(?:The number|The date) (.+?) does not occur")


def validate_change(reply, source, coming=False, final=False, day=None, changed=None):
    if not isinstance(reply, dict) or not isinstance(reply.get("sentence"), str) or not squash(reply["sentence"]):
        raise Unclassified("The reply must hold the sentence.")
    if day and not final and names_day(reply["sentence"], day):
        raise Unclassified("Do not write the day from applies_from: it is shown next to your sentence. Write only "
                           "what will be different.")
    if coming and not final and PAST.search(reply["sentence"]):
        raise Unclassified("The change applies only from applies_from on. Write what WILL be different, in the "
                           "present or future tense ('entfällt künftig', 'gilt künftig', 'wird ersetzt'), "
                           "not 'wurde' or 'wurden'.")
    answer = {"sentence": annex_or_appendix(german_places(squash(reply["sentence"])), source)}
    marks = base.number_marks([answer["sentence"]], source)
    if changed is not None:
        # A value that stands only in the unchanged words around a difference (for example the next row of
        # a table) is described as changed by mistake.
        values = [match.group(1) for mark in base.number_marks([answer["sentence"]], changed)
                  if mark not in marks and (match := VALUE_IN_MARK.match(mark))]
        if values and not final:
            raise Unclassified(f"These values stand only in before or after, which did not change: "
                               f"{', '.join(values)}. Describe only what differs between old and new.")
        marks = [*marks, *(f"The value {value} stands only in the unchanged words around a change."
                           for value in values)]
    if marks:
        answer["number_check"] = marks
    return answer


def change_sentence(client, system, sent):
    log = []
    source = json.dumps(sent, ensure_ascii=False)
    changed = changed_only(sent)
    coming = bool(sent.get("applies_from"))
    answer = base.ask(client, system, sent, CHANGE_TASK, CHANGE_SCHEMA, "change_summary",
                      lambda reply, final=False: validate_change(reply, source, coming, final,
                                                                 sent.get("applies_from"), changed), log, 600)
    return answer, log


# ------------------------------------------------------------------ lists of standards (no AI)

STANDARD = (r"(?:EN|HD|ES|CEN|CLC|ETSI|ISO|IEC)(?: (?:ISO|IEC)(?:/IEC)?)?(?:/TS|/TR)? [0-9][0-9A-Za-z.-]*"
            r"(?: S\d+)?:\d{4}(?:\+A\d+:\d{4})*")
NEXT_ENTRY = re.compile(rf"(?<!\S)\d+[a-z]*\.\s+{STANDARD}")
POINT_NUMBER = re.compile(r"\bpoint (\d+[a-z]*)$")
MAX_NAMES = 8


def standard_entry(line, side):
    """(reference, [amendments]) of one entry of a list of standards ("155a. EN 14129:2024 LPG equipment ...
    EN 14129:2024/A1:2025"), cut by its point number from the stored wording; None when the place is not a
    numbered point or the wording does not hold exactly one such entry."""
    text = plain(line.get("new_text" if side == "new" else "old_text"))
    point = POINT_NUMBER.search(line.get("place") or "")
    if not point or not text:
        return None
    found = list(re.finditer(rf"(?<!\S){re.escape(point.group(1))}\.\s+({STANDARD})", text))
    if len(found) != 1:
        return None
    following = NEXT_ENTRY.search(text, found[0].end())
    piece = text[found[0].start(1):following.start() if following else len(text)]
    reference = found[0].group(1)
    amendments = re.findall(re.escape(reference) + r"/((?:A|AC)\d*:\d{4})", piece)
    return reference, list(dict.fromkeys(amendments))


def _names(items, kind, unread=0):
    """Up to MAX_NAMES references; amendments are named for standards added or renewed, not for deleted ones.
    unread: entries of the same kind whose reference could not be read from the stored wording."""
    shown = []
    for reference, amendments in items[:MAX_NAMES]:
        joined = ", ".join(amendments)
        if kind == "renewed" and amendments:
            shown.append(f"{reference} (jetzt mit {joined})")
        elif kind == "added" and amendments:
            shown.append(f"{reference} (mit {joined})")
        else:
            shown.append(reference)
    others = max(0, len(items) - MAX_NAMES) + unread
    if not shown:
        return f"{others} {'Eintrag' if others == 1 else 'Einträge'}"
    text = ", ".join(shown)
    return f"{text} sowie {others} weitere" if others else text


LIST_LABELS = {False: {"removed": "Aus der Liste gestrichen", "added": "Neu in der Liste", "renewed": "Neu gefasst"},
               True: {"removed": "Künftig entfallen", "added": "Künftig neu in der Liste",
                      "renewed": "Künftig neu gefasst"}}
ADDING, DELETING = ("ADD", "INSERT"), ("DELETE", "REPEAL")


def standards_sentence(lines, coming=False):
    """The sentence for a day on which only numbered entries of a list of standards were added or deleted,
    written without AI from the references in the wording: "Aus der Liste gestrichen: EN 144-3:2003, ...
    Neu in der Liste: ...". A standard deleted and added again on the same day (a new entry with an
    amendment) is "neu gefasst". An entry whose reference cannot be read (no wording, or a text cut before
    it) is counted ("sowie 4 weitere"). None when one change is anything else, or no reference at all can be
    read: Qwen writes the sentence then."""
    added, removed, unread = [], [], Counter()
    for line in lines:
        if line["operation"] in ADDING and not line.get("old_text"):
            found, target, kind = standard_entry(line, "new"), added, "added"
        elif line["operation"] in DELETING and not line.get("new_text"):
            found, target, kind = standard_entry(line, "old"), removed, "removed"
        else:
            return None
        if not POINT_NUMBER.search(line.get("place") or ""):
            return None
        if found is None:
            unread[kind] += 1
        else:
            target.append(found)
    if not (added or removed):
        return None
    again = {reference for reference, _ in added} & {reference for reference, _ in removed}
    groups = {"removed": [item for item in removed if item[0] not in again],
              "added": [item for item in added if item[0] not in again],
              "renewed": [item for item in added if item[0] in again]}
    parts = [f"{LIST_LABELS[coming][kind]}: {_names(items, kind, unread[kind])}"
             for kind, items in groups.items() if items or unread[kind]]
    return ". ".join(parts) + "."


HEADLINE_SYSTEM = ("You translate the headline of a provision of an EU legal act into German. Keep names, numbers, "
                   "standards and codes (for example M1, N1, EN 14129:2024) as they are. Use precise legal "
                   "German terms. Return one JSON object with headline.")
HEADLINE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["headline"],
                   "properties": {"headline": {"type": "string"}}}


def german_headline(client, headline, audit):
    """An English headline (Qwen sometimes keeps the language of the text) translated into German. The
    English one stays when the translation is still English."""
    def check(reply):
        if not isinstance(reply, dict) or not squash(reply.get("headline") or ""):
            raise Unclassified("The reply must hold the headline.")
        return squash(reply["headline"])

    log = audit.setdefault("headline_requests", [])
    try:
        answer = base.ask(client, HEADLINE_SYSTEM, {"headline": headline}, "TASK: Translate the headline.",
                          HEADLINE_SCHEMA, "headline", check, log, 200)
    except Unclassified:
        return headline
    return headline if foreign(answer) else answer


def what_changed(unit):
    """The fact lines (no AI) of the latest change, or of the last change before --since."""
    lines = latest_lines(unit)
    if lines:
        facts = [fact_line(line) for line in lines[:MAX_FACT_LINES]]
        if len(lines) > MAX_FACT_LINES:
            facts.append(f"und {len(lines) - MAX_FACT_LINES} weitere Stellen am selben Tag")
        return facts
    history = [line for line in unit["root"]["changes"] if line["role"] == "history"]
    if history:
        last = history[0]  # newest first
        return [f"Zuletzt geändert am {day_de(last['date'])} durch {act_name(last['changing_act'])}"]
    return []


def summarise_unit(client, systems, unit):
    """The record of one changed article or annex."""
    root = unit["root"]
    kind = unit_kind(unit)
    record = {key: unit[key] for key in ("celex", "title", "act_level", "today_version", "today_version_celex",
                                          "key", "provisions", "tables")}
    record.update(version=VERSION, kind=kind, name=root["name"], today=_today_facts(root),
                  deleted=root.get("deleted"),
                  what_changed={"facts": what_changed(unit)},
                  upcoming=[upcoming_line(line) for line in root["changes"] if line["role"] == "upcoming"],
                  changes={role: [_card(line) for line in root["changes"] if line["role"] == role]
                           for role in ("upcoming", "latest", "history", "no date")})
    if kind == "deleted":
        removed = root["deleted"]
        record["what_changed"]["facts"] = [f"Gestrichen am {day_de(removed['date'])} durch {act_name(removed['by'])}"]
    if kind == "summary":
        summary, record["audit"] = base.summarise_provision(
            client, systems["provision"], saved_pack(unit, text_node(unit)), systems["merge"])
        # Tidied as the version-1 cards are (point kinds, fixed German terms); nothing is cut.
        record["summary"] = base.tidy_summary(summary, root["today"]["text"][:600], base.OUTPUT_LANGUAGE)
        if base.OUTPUT_LANGUAGE == "de" and foreign(record["summary"].get("headline")):
            record["summary"]["headline"] = german_headline(client, record["summary"]["headline"], record["audit"])
        if base.OUTPUT_LANGUAGE == "de" and any(foreign(record["summary"].get(key)) for key in ("headline", "overview")):
            record["summary"]["language_check"] = True   # marked for the hand check, as numbers are
    template = standards_sentence(latest_lines(unit)) if kind != "deleted" and latest_lines(unit) else None
    sent = change_input(unit) if kind != "deleted" and template is None else None
    if template:
        record["what_changed"].update(sentence=template, sentence_by="template")
    if sent is not None:
        answer, log = change_sentence(client, systems["change"], sent)
        if base.OUTPUT_LANGUAGE:
            answer["sentence"] = base.fix_terms(answer["sentence"], base.OUTPUT_LANGUAGE)
        record["what_changed"].update(answer)
        record.setdefault("audit", {})["change_requests"] = log
    # Upcoming changes: one sentence per day on what will be different (not part of the summary).
    coming = []
    for day, lines in upcoming_days(unit).items():
        item = {"date": day, "line": upcoming_line(lines[0])
                + (f" – und {len(lines) - 1} weitere Stellen" if len(lines) > 1 else "")}
        template = standards_sentence(lines, coming=True)
        sent = change_input(unit, lines, day) if template is None else None
        if template:
            item.update(sentence=template, sentence_by="template")
        if sent is not None:
            answer, log = change_sentence(client, systems["change"], sent)
            if base.OUTPUT_LANGUAGE:
                answer["sentence"] = base.fix_terms(answer["sentence"], base.OUTPUT_LANGUAGE)
            item.update(answer)
            record.setdefault("audit", {}).setdefault("upcoming_requests", []).extend(log)
        coming.append(item)
    if coming:
        record["upcoming_explained"] = coming
    return record


def _today_facts(root):
    today = root.get("today") or {}
    return {key: today.get(key) for key in ("version_date", "version_kind", "link", "heading")} | {
        "characters": len(today.get("text") or "")}


def _card(line):
    keep = ("role", "date", "operation", "relation", "place", "where", "changing_act", "changing_act_link",
            "status", "complete", "note", "old_text", "new_text", "old_is_context", "old_link", "new_link")
    card = {key: line.get(key) for key in keep if line.get(key) not in (None, "")}
    card["line"] = upcoming_line(line) if line["role"] == "upcoming" else fact_line(line)
    return card


# ------------------------------------------------------------------ output

def unit_path(out, unit):
    return out / "units" / safe_name(unit["celex"]) / f"{unit['key'].replace('/', '_')}.json"


def saved_record(out, unit):
    path = unit_path(out, unit)
    record = read_json(path) if path.is_file() else {}
    return record if record.get("version") == VERSION and "failure" not in record else None


REVIEW_FIELDS = ["celex", "provision", "relevant", "level", "kind", "today_version", "headline", "overview",
                 "bullets", "was_hat_sich_geaendert", "sentence", "upcoming", "number_check", "failure",
                 "summary correct (yes/no)", "what changed correct (yes/no)", "comment"]


def export(out, units):
    records, rows, counts = [], [], Counter()
    for unit in units:
        path = unit_path(out, unit)
        record = read_json(path) if path.is_file() else None
        if record is None:
            counts["not done yet"] += 1
            continue
        records.append(record)
        counts[record.get("kind", "?")] += 1
        counts["failed"] += "failure" in record
        summary = record.get("summary") or {}
        changed = record.get("what_changed") or {}
        rows.append({
            "celex": record["celex"], "provision": record["name"],
            "relevant": " | ".join(p["locator"] for p in record["provisions"] + record["tables"]),
            "level": ",".join(sorted({p["level"] for p in record["provisions"] + record["tables"] if p.get("level")})),
            "kind": record.get("kind"), "today_version": record.get("today_version"),
            "headline": summary.get("headline", ""), "overview": summary.get("overview", ""),
            "bullets": " | ".join(f"{b['kind']}: {b['text']}" for b in summary.get("bullets", [])),
            "was_hat_sich_geaendert": " | ".join(changed.get("facts", [])),
            "sentence": changed.get("sentence", ""),
            "upcoming": " | ".join(f"{item['line']}: {item.get('sentence', '')}".rstrip(": ")
                                   for item in record.get("upcoming_explained", []))
                        or " | ".join(record.get("upcoming", [])),
            "number_check": "; ".join(str(m) for m in [*summary.get("number_check", []),
                                                        *changed.get("number_check", [])]),
            "failure": record.get("failure", "")
            + ("language: English text" if summary.get("language_check") else "")})
    write_json(out / "changed_provisions.json", records)
    with (out / "review.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    preview(out, records)
    summary = {"units": len(units), **counts}
    write_json(out / "summary.json", summary)
    return summary


STYLE = """body{font-family:Segoe UI,Arial,sans-serif;margin:24px auto;max-width:980px;color:#1c2430;background:#f5f6f8}
.unit{background:#fff;border:1px solid #d9dde3;border-radius:8px;padding:14px 18px;margin:14px 0}
h1{font-size:22px}h2{font-size:17px;margin:0 0 4px}.meta{color:#5b6573;font-size:13px}
.changed{background:#eef6ff;border-left:4px solid #2f6fdf;padding:8px 12px;margin:10px 0}
.upcoming{background:#fff4e5;border-left:4px solid #e08a00;padding:6px 12px;margin:6px 0}
.card{border:1px solid #e3e6ea;border-radius:6px;padding:8px 10px;margin:8px 0;font-size:14px}
.old{background:#fdecec;color:#8a1c1c;text-decoration:line-through;white-space:pre-wrap;padding:6px}
.context{background:#f4f4f4;color:#444;white-space:pre-wrap;padding:6px}
.new{background:#e9f7ec;color:#14532d;white-space:pre-wrap;padding:6px}
details{margin-top:8px}a{color:#2f6fdf}.check{color:#a15c00;font-size:12px}"""
KIND_NOTE = {"same_text": "Kein neuerer Text: die bisherige Zusammenfassung gilt weiter.",
             "no_text": "Der heutige Text konnte nicht ausgeschnitten werden: die bisherige Zusammenfassung gilt "
                        "weiter, bitte den Originaltext öffnen.",
             "tables": "Relevant sind Tabellen dieses Anhangs: ihre bisherigen Zusammenfassungen gelten weiter."}


def _wording(card):
    parts = []
    if card.get("old_text"):
        css = "context" if card.get("old_is_context") else "old"
        label = "Bisher (mit umgebendem Text)" if card.get("old_is_context") else "Bisher"
        parts.append(f"<div>{label}:</div><div class='{css}'>{html.escape(card['old_text'][:6000])}</div>")
    if card.get("new_text"):
        parts.append(f"<div>Neu:</div><div class='new'>{html.escape(card['new_text'][:6000])}</div>")
    links = [f"<a href='{html.escape(card[key])}'>{label}</a>" for key, label in
             (("new_link", "Stelle auf EUR-Lex"), ("changing_act_link", "Ändernder Rechtsakt")) if card.get(key)]
    return f"<div class='card'><b>{html.escape(card['line'])}</b>{''.join(parts)}<div>{' · '.join(links)}</div></div>"


def unit_html(record):
    summary, changed = record.get("summary") or {}, record.get("what_changed") or {}
    today = record.get("today") or {}
    out = [f"<div class='unit'><h2>{html.escape(record['name'])}</h2>",
           f"<div class='meta'>Relevant: {html.escape(', '.join(p['locator'] for p in record['provisions'] + record['tables']))}"
           + (f" · Stand {html.escape(day_de(today['version_date']))} <a href='{html.escape(today['link'])}'>EUR-Lex</a>"
              if today.get("version_date") and today.get("link") else "") + "</div>"]
    if summary:
        out.append(f"<p><b>{html.escape(summary.get('headline', ''))}</b><br>{html.escape(summary.get('overview', ''))}</p>")
        out.append("<ul>" + "".join(f"<li>{html.escape(b['text'])}</li>" for b in summary.get("bullets", [])) + "</ul>")
    elif record.get("kind") in KIND_NOTE:
        out.append(f"<p class='meta'>{KIND_NOTE[record['kind']]}</p>")
    if changed.get("facts") or changed.get("sentence"):
        out.append("<div class='changed'><b>Was hat sich geändert</b><br>"
                   + "<br>".join(html.escape(fact) for fact in changed.get("facts", []))
                   + (f"<br><i>{html.escape(changed['sentence'])}</i>" if changed.get("sentence") else "")
                   + (f"<div class='check'>Zahl prüfen: {html.escape(str(changed['number_check']))}</div>"
                      if changed.get("number_check") else "") + "</div>")
    explained = record.get("upcoming_explained")
    if explained:
        out += [f"<div class='upcoming'><b>{html.escape(item['line'])}</b>"
                + (f"<br><i>{html.escape(item['sentence'])}</i>" if item.get("sentence") else "")
                + (f"<div class='check'>Zahl prüfen: {html.escape(str(item['number_check']))}</div>"
                   if item.get("number_check") else "") + "</div>" for item in explained]
    else:
        out += [f"<div class='upcoming'>{html.escape(line)}</div>" for line in record.get("upcoming", [])]
    cards = record.get("changes", {})
    out += [_wording(card) for card in cards.get("upcoming", []) + cards.get("latest", [])]
    earlier = cards.get("history", []) + cards.get("no date", [])
    if earlier:
        out.append(f"<details><summary>Frühere Änderungen ({len(earlier)})</summary>"
                   + "".join(f"<div>{html.escape(card['line'])} · <a href='{html.escape(card.get('changing_act_link', ''))}'>"
                             "EUR-Lex</a></div>" for card in earlier) + "</details>")
    if record.get("failure"):
        out.append(f"<div class='check'>Fehler: {html.escape(record['failure'])}</div>")
    return "".join(out) + "</div>"


def preview(out, records):
    folder = out / "preview"
    folder.mkdir(parents=True, exist_ok=True)
    acts = defaultdict(list)
    for record in records:
        acts[record["celex"]].append(record)
    links = []
    for celex, items in sorted(acts.items()):
        items.sort(key=lambda r: base.locator_key(r["name"]))
        page = (f"<!DOCTYPE html><html lang='de'><meta charset='utf-8'><title>{html.escape(celex)}</title>"
                f"<style>{STYLE}</style><p><a href='index.html'>← Übersicht</a></p>"
                f"<h1>{html.escape(items[0]['title'] or celex)}</h1>"
                f"<div class='meta'>{html.escape(celex)} · Heute geltender Text: {html.escape(version_de(items[0].get('today_version')))}"
                f"</div>{''.join(unit_html(r) for r in items)}"
                f"<p class='meta'>{html.escape(base.NOTE['de'])}</p></html>")
        name = f"{safe_name(celex)}.html"
        (folder / name).write_text(page, encoding="utf-8")
        links.append(f"<li><a href='{name}'>{html.escape(celex)}</a> {html.escape(items[0]['title'][:140])} "
                     f"({len(items)})</li>")
    (folder / "index.html").write_text(
        f"<!DOCTYPE html><html lang='de'><meta charset='utf-8'><title>Geänderte Vorschriften</title>"
        f"<style>{STYLE}</style><h1>Geänderte relevante Vorschriften ({len(records)})</h1><ul>{''.join(links)}</ul></html>",
        encoding="utf-8")


# ------------------------------------------------------------------ run

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--changed-dir", type=Path, required=True,
                        help="Folder written by the backend: reports/changed-texts/<time>")
    parser.add_argument("--classification-dir", type=Path, default=DEFAULT_CLASSIFICATION)
    parser.add_argument("--extra-classification-dir", type=Path, nargs="*", default=[],
                        help="More classifications, for example of the articles inserted later "
                             "(screen_new_articles.py). Their provisions are added to the act.")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--levels", nargs="+", choices=KEPT, default=list(KEPT))
    parser.add_argument("--language", choices=tuple(base.LANGUAGES), default="de")
    parser.add_argument("--sample", type=int, help="Only this many articles or annexes (the first ones).")
    parser.add_argument("--celex", nargs="+", default=[], help="Only these acts.")
    parser.add_argument("--only-latest", action="store_true",
                        help="Only articles and annexes whose latest change is from 2015 on (shown with old/new).")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--count-only", action="store_true")
    args = parser.parse_args(argv)
    base.OUTPUT_LANGUAGE = args.language
    out = args.output_dir.resolve()
    try:
        systems = {name: base.system_prompt(name, args.language) for name in ("provision", "merge")}
        systems["change"] = CHANGE_PROMPT.read_text(encoding="utf-8").replace(
            "<LANGUAGE>", base.LANGUAGES[args.language]).strip() + "\n"
        classified = merged([args.classification_dir, *args.extra_classification_dir])
        units, facts = plan(args.changed_dir, classified, set(args.levels), set(args.celex))
    except (OSError, ValueError, KeyError) as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 1
    if args.only_latest:
        units = [unit for unit in units if latest_lines(unit)]
    if args.sample:
        units = pick_sample(units, args.sample)
    kinds = Counter(unit_kind(unit) for unit in units)
    print(f"Changed articles and annexes with a relevant provision: {len(units):,} in "
          f"{len({u['celex'] for u in units}):,} acts.", flush=True)
    print("  " + ", ".join(f"{key} {value:,}" for key, value in sorted(facts.items())), flush=True)
    print("  " + ", ".join(f"{key} {value:,}" for key, value in sorted(kinds.items())), flush=True)
    if args.count_only or not units:
        return 0
    write_json(out / "run_config.json", {"version": VERSION, "language": args.language,
                                         "changed_dir": str(args.changed_dir.resolve()),
                                         "classification": str(args.classification_dir),
                                         "system_prompts": systems})
    client = QwenClient.from_env(args.env_file, retries=1)
    workers = max(1, min(args.workers, MAX_WORKERS))

    def open_units():
        todo = [unit for unit in units if saved_record(out, unit) is None]
        return todo, len(units) - len(todo)

    def worker(progress):
        def work(unit):
            failed = False
            try:
                record = summarise_unit(client, systems, unit)
            except ConnectionLost:
                raise
            except Exception as exc:  # noqa: BLE001 - one unreadable article must not stop the run
                record = {key: unit[key] for key in ("celex", "title", "key", "provisions", "tables")}
                record.update(version=VERSION, name=unit["root"]["name"], kind=unit_kind(unit),
                              failure=f"{type(exc).__name__}: {exc}"[:400])
                failed = True
                print(f"Could not summarise {unit['celex']} {unit['root']['name']}: {record['failure']}", flush=True)
            write_json(unit_path(out, unit), record)
            progress.step(failed)
        return work

    try:
        ok, error = reachable(client)
        if not ok:
            wait_until_reachable(client, error)
        run_until_done(client, "Changed", open_units, worker, workers, after_pass=lambda: export(out, units))
    except KeyboardInterrupt:
        export(out, units)
        print("\nStopped by Ctrl+C. Run the same command to continue.", flush=True)
        return 130
    summary = export(out, units)
    print(f"\nFinished: {summary}")
    print(f"Open in a browser: {out / 'preview' / 'index.html'}\nTo check by hand: {out / 'review.csv'}\n"
          f"For the database import: {out / 'changed_provisions.json'}")
    if summary.get("failed"):
        print("Run the same command again to retry what failed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
