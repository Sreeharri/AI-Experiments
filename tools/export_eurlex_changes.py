"""Export the changes between EUR-Lex acts (amendments, repeals, corrigenda, ...) for the AI step.

Read-only. Reads the backend tables with SQL; no backend code is used or needed.

For every operational relationship "changing act -> changed act" where at least one of the two acts
is relevant (it has results in the classification), it writes one record per provision change, one
record per act-level relationship without provision detail, and one record per corrigendum:

    kind         PROVISION | ACT | CORRIGENDUM
    changing     {celex, title, date, entry_into_force, application, url}
    changed      {celex, title}
    relation     AMENDS, REPEALS, CORRECTS, ...
    operation    ADD, INSERT, REPLACE, DELETE, ... (PROVISION only)
    provision    "Article 15" (PROVISION only), root locator ("article", "15")
    effective_from
    old_text     the wording before the change, exactly as the UI shows it (reviewed comparison, else the
                 backend's matcher app/services/eurlex_wording.py, loaded read-only with --backend-dir)
    new_text     the wording after the change, the same way
    comparison_status  the backend's status (MATCHED_STORED_EVIDENCE, PREVIOUS_WORDING_UNRESOLVED, ...)
    instruction  the passage of the changing act that makes this change
    final_articles  entry into force, application, transposition and transitional articles of the changing act
    corrections  [{location, wrong, correct}] (CORRIGENDUM only)
    wording      FULL | NEW_ONLY | OLD_ONLY | INSTRUCTION_ONLY | NONE

Version 1.7: also the article heading and the current wording of the exact place (to know what a point is about).
Version 1.4: old/new wording from the backend's own matcher (same as the UI); roles.json from all
relationships; one date with its source per change. Since 1.2: corrigenda are only taken from real corrigendum text (else read from EUR-Lex, German first, with
--fetch-missing); old texts come only from versions with no other change in between; long texts keep their
changed parts; the changing act's own article is never taken for the change; "Annex" without number = sole annex.

    python tools/export_eurlex_changes.py --count-only
    python tools/export_eurlex_changes.py --since 2015 --fetch-missing --out runs/eurlex_changes_export_v5
    python tools/export_eurlex_changes.py --celex 32010L0075 --out runs/eurlex_changes_ied
"""

from __future__ import annotations

import argparse
import difflib
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
FORMAT = "eurlex_changes_v2"

# Relationships that change the text or the application of another act.
TEXT_CHANGES = {"AMENDS", "REPLACES", "REPEALS", "IMPLICITLY_REPEALS", "CORRECTS", "DEROGATES", "SUSPENDS",
                "PARTIALLY_SUSPENDS", "EXTENDS_APPLICATION", "EXTENDS_VALIDITY", "DEFERS_APPLICATION",
                "REESTABLISHES", "RENDERS_OBSOLETE"}
# Relationships that add rules next to another act without changing its text (shown as a link only).
LINKS_ONLY = {"COMPLETES", "ADDS_TO", "IMPLEMENTS", "INCORPORATES", "CONFIRMS", "ADOPTS", "PARTIALLY_ADOPTS"}

STOP_FETCH_AFTER = 25       # EUR-Lex not reachable this often in a row (and never reached): stop asking
TEXT_LIMIT = 12000
CONTEXT_LIMIT = 1500        # current wording of the exact place, to know what a point is about
ARTICLE_LIMIT = 5000        # the whole changed article or annex (before the change and now), as background
INSTRUCTION_LIMIT = 6000
FINAL_ARTICLE_LIMIT = 1500
MAX_FINAL_ARTICLES = 4

ROOT_PATTERN = re.compile(r"\b(Article|Annex|Artikel|Anhang)\s+([0-9]+[a-z]*|[IVXLCDM]+[a-z]*)\b", re.I)
FINAL_ARTICLE = re.compile(  # only clear final-article words, looked for in the heading and the first words
    r"entry into force|enter into force|shall apply from|shall apply as from|\bTransposition\b|"
    r"Member States shall bring into force|Transitional provisions?|Inkrafttreten|\bUmsetzung\b|"
    r"Übergangsbestimmung|gilt ab", re.I)
LAST_ARTICLES = 6           # final articles are looked for among the last articles of the act
MARKERS = re.compile(r"[▼►◄]\s*(?:M\d+|B|C\d*|A\d+)\b")
QUOTE = re.compile(r"[‘“„\"']\s*(.+?)\s*[’”“\"']\s*(?=[;.:,]?\s*(?:\(\d+[a-z]?\)|$|\n|[a-z]\)))", re.S)
OPEN_Q, CLOSE_Q = "‘“„«\"'", "’”“»\"'"
ACT_BLOCK = re.compile(  # "Directive 2010/75/EU is amended as follows" / "In Regulation (EU) No 321/2013,"
    r"(?:(?:Directive|Regulation|Decision)[^.;:]{0,80}?(?<![\d/])(\d{1,4})/(\d{1,4})(?![\d])[^.;:]{0,60}?"
    r"(?:is|are) (?:hereby )?(?:amended|corrected)|\bIn (?:Directive|Regulation|Decision|Implementing Regulation|"
    r"Delegated Regulation)[^.;:]{0,60}?(?<![\d/])(\d{1,4})/(\d{1,4})(?![\d]))", re.I)
POINT_START = re.compile(r"(?:^|(?<=\s))\(\d+[a-z]?\)\s+(?=[A-Za-z‘])")
# "(2) in Annex I, point 21 ... is replaced by the following": clearly the next instruction, even when the quote of
# the point before was never closed (footnote marks often break the closing quote).
NEXT_INSTRUCTION = re.compile(r"in\s+(?:Article|Annex|Artikel|Anhang)\s+[0-9IVXLCDM]+[a-z]*\b[^‘“„]{0,200}?"
                              r"\b(?:is|are)\s+(?:replaced|amended|deleted|inserted|added)\b", re.I)
# Places that are not an article or an annex: "Chapter III", "Section 2", "Title" (the act's title).
PLACE_WORD = re.compile(r"\b(Chapter|Section|Part|Title|Recital|Kapitel|Abschnitt|Teil|Titel)\b"
                        r"(?:\s+([0-9]+[a-z]*|[IVXLCDM]+[a-z]?)\b)?", re.I)
CHANGE_VERBS = r"(?:replaced|deleted|inserted|added|amended|repealed|corrected|renumbered|supplemented|modified)"
_LOC = r"(?:On page|Page|Auf Seite|Seite)\s+\d"
_END = rf"(?=\s*[.;,]?\s*(?:$|\n|{_LOC}|(?:for|instead of|anstatt|statt)\s*:))"
CORRECTION = re.compile(
    r"(?P<location>(?:On page|Page|Auf Seite|Seite)\s+\d[^:]{0,240}?)?[:,]?\s*"
    r"(?:for|instead of|anstatt|statt)\s*:?\s*[‘“„«\"'](?P<wrong>.+?)[’”“»\"']\s*[,;]?\s*"
    r"(?:read|muss es heißen|muss es lauten|lies|richtig)\s*:?\s*[‘“„«\"'](?P<correct>.+?)[’”“»\"']" + _END,
    re.S | re.I)
WHOLE_CORRECTION = re.compile(  # "On page 12, Annex II is replaced by the following: ..." / "erhält folgende Fassung"
    rf"(?P<location>(?:On page|Page|Auf Seite|Seite)\s+\d[^:]{{0,240}}?)\s*"
    rf"(?:(?:is|are) replaced by the following|(?:is|are) to read as follows|erhält folgende Fassung|"
    rf"erhalten folgende Fassung)\s*:?\s*(?P<correct>.+?)(?={_LOC}|$)", re.S | re.I)
CHANGE_VERB = re.compile(r"\b(?:replaced|inserted|added|deleted|amended|repealed|corrected|reads?|"
                         r"ersetzt|eingefügt|angefügt|gestrichen|geändert|aufgehoben|Fassung)\b", re.I)
CORRIGENDUM_WORDS = re.compile(r"\b(?:Corrigendum|Berichtigung)\b", re.I)


# ------------------------------------------------------------------ text helpers (no database)

def clean(text: str | None) -> str:
    """Remove publisher markers (▼M1 ...) and squash white space."""
    return re.sub(r"\s+", " ", MARKERS.sub(" ", text or "")).strip()


def signature(text: str | None) -> str:
    return re.sub(r"[^0-9a-z]", "", clean(text).casefold())


def root_of(locator: str | None) -> tuple[str, str] | None:
    """("article", "15") for "Article 15(3)(b)", ("annex", "ii") for "Annex II, point 2".
    An annex without number ("Annex", "Annex TXT", "Annex PRT A") is the act's only annex: ("annex", "")."""
    match = ROOT_PATTERN.search(locator or "")
    if not match:
        if re.match(r"^\s*(?:Annex|Anhang)\b(?!\s+(?:[0-9]+|[IVXLCDM]+)\b)", locator or "", re.I):
            return "annex", ""
        return None
    kind = "article" if match.group(1).casefold() in {"article", "artikel"} else "annex"
    return kind, match.group(2).casefold()


def act_number(celex: str) -> str | None:
    """The number the act is cited by: 32010L0075 -> 2010/75."""
    match = re.match(r"^[0-9](\d{4})[A-Z]{1,2}(\d{4})", celex or "")
    return f"{match.group(1)}/{int(match.group(2))}" if match else None


def act_forms(celex: str) -> set[str]:
    """Every way the act is cited: 2010/75 (directives, newer acts), 321/2013 (older regulations:
    "Regulation (EU) No 321/2013"), 89/391 and 1408/71 (two-digit years before 1999)."""
    match = re.match(r"^[0-9](\d{4})[A-Z]{1,2}(\d{4})", celex or "")
    if not match:
        return set()
    year, number = int(match.group(1)), int(match.group(2))
    forms = {f"{year}/{number}", f"{number}/{year}"}
    if year < 1999:
        forms |= {f"{year % 100:02d}/{number}", f"{number}/{year % 100:02d}"}
    return forms


def cites(text: str, forms: set[str]) -> bool:
    return any(re.search(rf"(?<![\d/]){re.escape(form)}(?!\d)", text) for form in forms)


def mentions(text: str, root: tuple[str, str]) -> bool:
    kind, number = root
    words = "Article|Artikel" if kind == "article" else "Annex|Anhang"
    if not number:  # the only annex: "the Annex is replaced"
        return bool(re.search(rf"\bthe\s+(?:{words})\b(?!\s+(?:[0-9]+|[IVXLCDM]+)\b)|\b(?:{words})\s+(?:is|are|to)\b",
                              text, re.I))
    return bool(re.search(rf"\b(?:{words})\s+{re.escape(number)}(?![0-9a-z])", text, re.I))


def split_points(text: str) -> list[str]:
    """Split an amending article into its numbered points (1), (2), ... but never inside quoted new text."""
    cuts = [0]
    for match in POINT_START.finditer(text):
        before = text[cuts[-1]:match.start()]
        if before.count("‘") - before.count("’") <= 0 and before.count("“") - before.count("”") <= 0 or \
                NEXT_INSTRUCTION.match(text, match.end()):
            cuts.append(match.start())
    cuts.append(len(text))
    return [text[a:b].strip() for a, b in zip(cuts, cuts[1:]) if text[a:b].strip()]


def sentence_start(text: str, index: int) -> int:
    """Where the sentence holding `index` starts, so "Annex II to Regulation X is amended" stays whole."""
    cut = max(text.rfind(mark, 0, index) for mark in (". ", "; ", ": "))
    heading = re.match(r"\s*(?:Article|Artikel)\s+\d+[a-z]*\s+", text[max(cut, 0):index])
    start = cut + 2 if cut >= 0 else 0
    return start + (heading.end() if heading and cut < 0 else 0)


def act_blocks(text: str, forms) -> list[str]:
    """The parts of an amending text that change the act cited as `forms` (an amending act often changes
    several acts). `forms`: a set from act_forms(), or one number string."""
    forms = {forms} if isinstance(forms, str) else (forms or set())
    starts = [(sentence_start(text, m.start()), f"{int(m.group(1) or m.group(3))}/{int(m.group(2) or m.group(4))}")
              for m in ACT_BLOCK.finditer(text)]
    if not starts or not forms:
        return [text]
    blocks = [text[start:(starts[i + 1][0] if i + 1 < len(starts) else len(text))]
              for i, (start, cited) in enumerate(starts) if cited in forms]
    if starts[0][0] > 0 and all(cited in forms for _, cited in starts):
        blocks.insert(0, text[:starts[0][0]])
    return blocks


OPENERS, CLOSERS = "‘“„", "’”“"


def quotes_in(text: str) -> list[tuple[int, int]]:
    """(start, end) of the quoted new wording in an instruction. ’ followed by a letter is an apostrophe."""
    spans, stack, start = [], [], 0
    for index, char in enumerate(text):
        following = text[index + 1:index + 2]
        if char in CLOSERS and stack and not (char == "’" and following.isalnum()) and \
                not (char == "“" and stack[-1] != "„" and following.isalnum()):
            stack.pop()
            if not stack:
                spans.append((start, index + 1))
        elif char in OPENERS:
            if not stack:
                start = index
            stack.append(char)
    if stack:                       # cut off at the length limit: the quote runs to the end
        spans.append((start, len(text) + 1))
    return spans


def unquoted(text: str) -> str:
    """The instruction without its quoted new wording: what the changing act itself says."""
    parts, last = [], 0
    for start, end in quotes_in(text):
        parts.append(text[last:start])
        parts.append(" ‘…’ ")
        last = end
    parts.append(text[last:])
    return "".join(parts)


def quoted(text: str) -> list[str]:
    return [clean(text[start + 1:end - 1]) for start, end in quotes_in(text)]


def root_ref(root: tuple[str, str]) -> str:
    """"Article 4", also inside a list: "Articles 3, 4 and 5", "Annexes I, III, VI and VIII"."""
    kind, number = root
    words = "Articles?|Artikel" if kind == "article" else "Annex(?:es)?|Anh(?:a|ä)nge?"
    if not number:
        return rf"(?:{words})(?!\s+(?:[0-9]+|[IVXLCDM]+)\b)"
    other = r"(?:[0-9]+[a-z]*|[IVXLCDM]+[a-z]?)"
    return rf"(?:{words})\s+(?:{other}\s*(?:,|and|und|to|bis)\s*)*{re.escape(number)}(?![0-9a-z])"


def changes_root(point: str, root: tuple[str, str]) -> bool:
    """True when this point of an amending text itself changes `root` ("(3) in Article 4, ...",
    "Article 4 is replaced by ...", "the following Article 11b is inserted: ‘Article 11b ...’").
    Article numbers inside the quoted new wording do not count: they are references of the new text."""
    ref, text = root_ref(root), unquoted(point)
    if names_place(text, ref):
        return True
    if root[0] == "article" and root[1].isdigit():   # "Chapter III, containing Articles 15 to 29, is deleted"
        for match in re.finditer(r"\b(?:Articles|Artikel)\s+(\d+)\s*(?:to|bis|–|-)\s*(\d+)\b", text, re.I):
            if int(match.group(1)) < int(root[1]) < int(match.group(2)) and names_place(text, re.escape(match.group(0))):
                return True
    if root[0] == "annex" and re.search(r"\b(?:is|are) amended (?:as set out in|in accordance with)\s+(?:the\s+)?"
                                        r"Annex(?:\s+[IVXLC0-9]+)?\s+to\s+this\b", text, re.I) \
            and not re.search(r"\b(?:Articles?|Artikel|Annex(?:es)?|Anh(?:a|ä)nge?)\s+[0-9IVXLC]", text.split(" amended ")[0]):
        return True                 # an annex changed by the changing act's own annex
    if re.search(r"\b(?:inserted|added|replaced|eingefügt|angefügt|ersetzt)\b", text, re.I):
        return any(re.match(rf"\s*{ref}", q, re.I) for q in quoted(point))
    return False


def names_place(text: str, ref: str) -> bool:
    """True when the (unquoted) text of an amending point changes the place matched by the pattern `ref`."""
    patterns = [rf"^\W*(?:\(\w+\)\s*)*(?:in|In)\s+{ref}",
                rf"{ref}[^.;:‘]{{0,160}}?\b(?:is|are|shall be)\s+(?:hereby\s+)?{CHANGE_VERBS}",
                rf"\bfollowing\s+{ref}",
                rf"(?:^|\W)(?:In\s+)?{ref}[^.;:‘]{{0,160}}?\b(?:wird|werden)\b[^.;:‘]{{0,100}}?"
                rf"(?:ersetzt|gestrichen|eingefügt|angefügt|geändert|aufgehoben|gefasst)"]
    return any(re.search(pattern, text, re.I if i else 0) for i, pattern in enumerate(patterns))


BOUNDARY = re.compile(r"(?:(?<=[;:])|(?<=[^\d\s.]\.))\s+(?=\S)")   # after ; : or a sentence end, not "81."


def around_place(text: str, place: str, limit: int = ARTICLE_LIMIT) -> str:
    """A long article cut to `limit` characters around the changed place: the place itself and as much as fits of
    the text just before and after it (cut at sentence or point ends), with "…" where text is left out. Place not in
    this version (for example a new point in the version before the change): the beginning of the article."""
    if len(text) <= limit:
        return text
    room = limit - 4                                  # for the two "… " marks
    position = text.find(place) if place else -1
    if position < 0:
        cut = max((m.start() for m in BOUNDARY.finditer(text, 0, room) if m.start() > room // 2), default=room)
        return text[:cut].rstrip() + " …"
    if len(place) >= room:
        return ("… " if position else "") + place[:room].rstrip() + " …"
    start = max(0, position - (room - len(place)) // 2)
    end = min(len(text), start + room)
    start = max(0, end - room)
    if start > 0:                                     # begin after a sentence or point end
        starts = [m.end() for m in BOUNDARY.finditer(text, start, position + 1)]
        start = starts[0] if starts else start
    if end < len(text):                               # end at a sentence or point end
        ends = [m.start() for m in BOUNDARY.finditer(text, position + len(place) - 1, end)]
        end = ends[-1] if ends else end
    return ("… " if start > 0 else "") + text[start:end].strip() + (" …" if end < len(text) else "")


def place_instruction(elements: list[dict], changed_celex: str, locator: str) -> tuple[str, str | None]:
    """(text, element identifier) of the points that change a place that is not an article or an annex:
    "Chapter III" ("(4) Chapter III, containing Articles 15 to 29, is deleted"), "Title" ("(1) the title is
    replaced by ..."). No such point: no instruction."""
    match = PLACE_WORD.search(locator or "")
    if not match:
        return "", None
    word, number = match.group(1), match.group(2)
    ref = rf"(?:the\s+)?{re.escape(word)}s?\s+{re.escape(number)}(?![0-9a-z])" if number else \
        rf"\bthe\s+{re.escape(word)}(?!\s+(?:[0-9]+|[IVXLCDM]+)\b)"
    forms = act_forms(changed_celex)
    for element in (e for e in elements if cites(e["text"], forms)):
        for block in act_blocks(element["text"], forms):
            hits = [p for p in split_points(block) if names_place(unquoted(p), ref)]
            if hits:
                return " ".join(hits)[:INSTRUCTION_LIMIT], element["identifier"]
    return "", None


def instruction_for(elements: list[dict], changed_celex: str, root: tuple[str, str] | None) -> tuple[str, str | None]:
    """(text, element identifier) of the changing act's points that change `root` of the changed act.

    Looks in the elements that cite the changed act (only in their part about that act), then in amending
    annexes. No point found: no instruction (a wrong instruction is worse than none)."""
    forms = act_forms(changed_celex)
    if root is None:
        return "", None
    citing = [e for e in elements if cites(e["text"], forms)]
    others = [e for e in elements if e not in citing and e["identifier"].casefold().startswith(("annex", "anhang"))]
    for group, use_blocks in ((citing, True), (others, False)):
        for element in group:
            for block in (act_blocks(element["text"], forms) if use_blocks else [element["text"]]):
                hits = [p for p in split_points(block) if changes_root(p, root)]
                if hits:
                    return " ".join(hits)[:INSTRUCTION_LIMIT], element["identifier"]
    return "", None


def quoted_new_text(instruction: str, operation: str, root: tuple[str, str] | None = None) -> str:
    """The quoted new wording of the instruction. With several quoted articles only the one of `root`."""
    if operation not in {"ADD", "INSERT", "REPLACE", "MODIFY"}:
        return ""
    if not re.search(r"replaced|inserted|added|shall read|read as follows|amended to read|Fassung|eingefügt|"
                     r"angefügt|ersetzt", unquoted(instruction), re.I):
        return ""
    found = [q for q in quoted(instruction) if len(q) > 3]
    if root:
        heading = re.compile(r"\s*(?:Article|Artikel|Annex|Anhang)\s+[0-9IVXLCDM]+[a-z]*\b", re.I)
        mine = [q for q in found if re.match(rf"\s*{root_ref(root)}", q, re.I)]
        found = mine or [q for q in found if not heading.match(q)]
    return clean(" ".join(found))[:TEXT_LIMIT]


ANNEX_TEXT = re.compile(r"\b(replaced by|set out in|contained in|text in|in accordance with|as set out in|"
                        r"as specified in)\s+(?:the\s+)?Annex(?:\s+([IVXLC]+|\d+))?\s+to\s+this\s+"
                        r"(?:Regulation|Directive|Decision)", re.I)


def changing_annex(instruction: str, elements: list[dict]) -> tuple[str, str] | None:
    """("NEW", text) when the new wording stands in an annex of the changing act ("replaced by the text in the
    Annex to this Regulation"); ("INSTRUCTION", text) when that annex holds the amendments."""
    match = ANNEX_TEXT.search(unquoted(instruction or ""))
    if not match:
        return None
    wanted = ("annex", (match.group(2) or "").casefold())
    annex = next((e for e in elements if root_of(e["identifier"]) == wanted), None)
    if annex is None and not match.group(2):
        annexes = [e for e in elements if (root_of(e["identifier"]) or ("",))[0] == "annex"]
        annex = annexes[0] if len(annexes) == 1 else None
    if annex is None:
        return None
    kind = "NEW" if match.group(1).casefold() in {"replaced by", "text in", "set out in", "contained in"} \
        and re.search(r"replaced", unquoted(instruction), re.I) else "INSTRUCTION"
    return kind, annex["text"]


def corrections_in(text: str) -> list[dict]:
    """The corrections of a corrigendum: location, wrong wording, correct wording ("" wrong = replaced as a whole)."""
    found, location = [], ""
    for match in CORRECTION.finditer(text):
        location = clean(match.group("location") or "") or location   # several for/read pairs under one page line
        found.append((match.start(), {"location": location[:200], "wrong": clean(match.group("wrong"))[:1500],
                                      "correct": clean(match.group("correct"))[:1500]}))
    for match in WHOLE_CORRECTION.finditer(text):
        found.append((match.start(), {"location": clean(match.group("location"))[:200], "wrong": "",
                                      "correct": clean(match.group("correct"))[:3000]}))
    found = [item for _, item in sorted(found, key=lambda pair: pair[0])]
    return found


def is_corrigendum_text(text: str) -> bool:
    """True for the text of a corrigendum; False for the corrected act's own text stored under the corrigendum."""
    return bool(corrections_in(text)) or bool(CORRIGENDUM_WORDS.search(text[:600]))


def changed_regions(old: str, new: str, limit: int = TEXT_LIMIT) -> tuple[str, str, bool]:
    """Long texts: keep only the sentences that differ (with one neighbour each), so that a change deep inside a
    long annex is not cut off. Returns (old, new, trimmed)."""
    if len(old) <= limit and len(new) <= limit:
        return old, new, False
    split = lambda text: [s for s in re.split(r"(?<=[.;:])\s+(?=[A-Z(‘“0-9])", text) if s]
    a, b = split(old), split(new)
    matcher = difflib.SequenceMatcher(None, [signature(x) for x in a], [signature(x) for x in b], autojunk=False)
    keep_a, keep_b = set(), set()
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            keep_a.update(range(max(0, i1 - 1), min(len(a), i2 + 1)))
            keep_b.update(range(max(0, j1 - 1), min(len(b), j2 + 1)))

    def join(sentences, keep):
        parts, used, gap = [], 0, False
        for index, sentence in enumerate(sentences):
            if index in keep and used + len(sentence) <= limit:
                parts.append(sentence)
                used += len(sentence)
                gap = False
            elif not gap:
                parts.append("…")
                gap = True
        return " ".join(parts)
    return join(a, keep_a), join(b, keep_b), True


def final_articles(elements: list[dict]) -> list[dict]:
    """Articles of the changing act on entry into force, application, transposition and transition."""
    chosen = []
    articles = [e for e in elements if e["identifier"].casefold().startswith(("article", "artikel"))]
    for element in articles[-LAST_ARTICLES:]:
        head = f"{element.get('heading') or ''} {element['text'][:300]}"
        if FINAL_ARTICLE.search(head):
            chosen.append({"locator": element["identifier"], "text": element["text"][:FINAL_ARTICLE_LIMIT]})
    return chosen[-MAX_FINAL_ARTICLES:]


def wording_of(old: str | None, new: str | None, instruction: str) -> str:
    if old and new:
        return "FULL"
    if new:
        return "NEW_ONLY"
    if old:
        return "OLD_ONLY"
    return "INSTRUCTION_ONLY" if instruction else "NONE"


def record_id(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()[:20]


def year_of(celex: str, day) -> int | None:
    if day:
        return day.year if isinstance(day, date) else int(str(day)[:4])
    match = re.match(r"^[0-9](\d{4})", celex or "")
    return int(match.group(1)) if match else None


# ------------------------------------------------------------------ database

RELATIONSHIPS_SQL = """
SELECT r.id::text AS id, r.relation_type, r.relation_family, r.status, r.valid_from, r.valid_to,
       COALESCE(ls.celex, sc.external_id) AS changing_celex, COALESCE(lt.celex, tc.external_id) AS changed_celex,
       r.source_document_id::text AS changing_document, r.target_document_id::text AS changed_document,
       COALESCE(sc.title_en, sc.canonical_title, sc.title_de) AS changing_title,
       COALESCE(tc.title_en, tc.canonical_title, tc.title_de) AS changed_title,
       COALESCE(sc.document_date, sc.publication_date) AS changing_date,
       COALESCE(tc.document_date, tc.publication_date) AS changed_date,
       sc.date_entry_into_force AS entry_into_force, sc.date_application AS application,
       sc.information_url AS changing_url, tc.information_url AS changed_url,
       sc.source AS changing_source, tc.source AS changed_source
FROM instrument_relationships r
JOIN source_documents sc ON sc.id = r.source_document_id
JOIN source_documents tc ON tc.id = r.target_document_id
LEFT JOIN legal_instruments ls ON ls.source_document_id = r.source_document_id
LEFT JOIN legal_instruments lt ON lt.source_document_id = r.target_document_id
WHERE r.relation_family = 'OPERATIONAL'
"""
CHANGES_SQL = """
SELECT id::text AS id, relationship_id::text AS relationship_id, ordinal, operation, raw_operation_code,
       target_provision, target_locator_type, target_locator_number, target_element_id::text AS target_element_id,
       resolution_status, raw_annotation, effective_from, effective_to
FROM relationship_provision_changes WHERE relationship_id = ANY(%(ids)s::uuid[]) ORDER BY relationship_id, ordinal
"""
COMPARISONS_SQL = """
SELECT change_id::text AS change_id, old_text, new_text, instruction_text
FROM act_wording_comparisons WHERE change_id = ANY(%(ids)s::uuid[]) AND upper(language) = 'EN'
"""
VERSIONS_SQL = """
SELECT v.id::text AS id, v.version_key, v.version_kind, v.version_date, v.is_current, v.is_preferred, v.language,
       v.source_url, v.content_sha256
FROM instrument_versions v JOIN legal_instruments li ON li.id = v.instrument_id
WHERE li.source_document_id = %(document)s::uuid AND upper(v.language) = 'EN'
"""
ELEMENTS_SQL = """
SELECT id::text AS id, version_id::text AS version_id, element_type, identifier, heading, body_text, path, depth,
       ordinal
FROM legal_elements WHERE version_id = ANY(%(ids)s::uuid[]) ORDER BY version_id, ordinal
"""


class Store:
    """Read-only access with a small cache per document."""

    def __init__(self, connection, fetch_dir: Path | None = None, wording=None):
        self.connection = connection
        self.wording = wording              # BackendWording: the backend's own matcher
        self.raw: dict[str, tuple[list[dict], list[dict]]] = {}
        self.indexes: dict[str, object] = {}
        self.cache: dict[str, tuple[list[dict], dict[str, list[dict]]]] = {}
        self.fetch_dir = fetch_dir          # None: never ask EUR-Lex
        self.fetched = Counter()
        self.fetch_errors = Counter()
        self.failures_in_a_row = 0

    def backend_index(self, document_id: str, identity: str):
        if document_id not in self.indexes:
            if document_id not in self.raw:
                self.cache.pop(document_id, None)
                self.document(document_id)
            versions, elements = self.raw[document_id]
            self.indexes[document_id] = self.wording.index(versions, elements, identity)
        return self.indexes[document_id]

    def backend_match(self, change: dict, rel: dict) -> dict:
        """The UI's wording for one change (backend match_stored on the same rows)."""
        changing = self.backend_index(rel["changing_document"], rel["changing_celex"])
        changed = self.backend_index(rel["changed_document"], rel["changed_celex"])
        try:
            return self.wording.match(change, changing, changed)
        except Exception as exc:  # noqa: BLE001 - one odd change must not stop the export
            return {"comparison_status": f"MATCH_ERROR {type(exc).__name__}"}

    def place_context(self, row: dict, rel: dict) -> tuple[str | None, str | None]:
        """(heading of the changed article, current wording of the exact place) from the newest stored version of
        the changed act. Only to understand what a point or paragraph is about (for example "(b) service contracts
        …"); never used as old or new wording."""
        if self.wording is None or not row.get("target_provision"):
            return None, None
        try:
            index = self.backend_index(rel["changed_document"], rel["changed_celex"])
            target = self.wording.module.locator(row["target_provision"])
        except Exception:  # noqa: BLE001 - context is optional
            return None, None
        if target is None:
            return None, None
        newest = sorted(index.versions, key=lambda v: (bool(v.get("is_current")), v.get("version_date") or date.min),
                        reverse=True)[:3]
        heading = context = None
        root = (target.kind, target.number)
        for version in newest:
            elements = index.elements.get(str(version["id"]), [])
            if heading is None:
                heading = next((clean(e.get("heading")) for e in elements if e.get("heading") and
                                root_of(e.get("identifier")) == root and not re.search(r"\(|paragraph|point",
                                                                                         e.get("identifier") or "")),
                               None)
            if context is None and target.tokens:
                try:
                    found = index.selection([version], target)
                except Exception:  # noqa: BLE001
                    found = None
                if found:
                    context = clean(found[0])[:CONTEXT_LIMIT]
            if heading and (context or not target.tokens):
                break
        return heading or None, context or None

    def article_texts(self, row: dict, rel: dict, day) -> tuple[dict | None, dict | None]:
        """The whole changed article or annex from the database: (before the change, now), each
        {"version_date", "text"}. Only background for Qwen to understand the place; the UI wording stays the
        backend's. Before = the newest version dated before the change (else the original); now = the newest version.
        Both from the same version: only "now". A long article keeps the part around the changed place (see
        around_place); a short one goes whole."""
        if self.wording is None or not row.get("target_provision"):
            return None, None
        try:
            index = self.backend_index(rel["changed_document"], rel["changed_celex"])
            target = self.wording.module.locator(row["target_provision"])
            whole = self.wording.module.Locator(target.kind, target.number, ()) if target else None
        except Exception:  # noqa: BLE001 - background text is optional
            return None, None
        if whole is None:
            return None, None
        day = _date(day) if day else None

        def first_text(versions):
            for version in versions:
                try:
                    found = index.selection([version], whole)
                except Exception:  # noqa: BLE001
                    found = None
                if found and clean(found[0]):
                    text = clean(found[0])
                    if len(text) > ARTICLE_LIMIT:
                        try:
                            place = index.selection([version], target) if target.tokens else None
                        except Exception:  # noqa: BLE001
                            place = None
                        text = around_place(text, clean(place[0]) if place else "")
                    when = version.get("version_date")
                    return {"version_id": str(version["id"]), "version_date": str(when)[:10] if when else None,
                            "text": text}
            return None
        newest = sorted(index.versions, key=lambda v: (bool(v.get("is_current")), v.get("version_date") or date.min),
                        reverse=True)
        try:
            earlier = index.before(day) if day else index.originals()
        except Exception:  # noqa: BLE001
            earlier = []
        now, before = first_text(newest), first_text(earlier)
        if before and now and (before["version_id"] == now["version_id"] or signature(before["text"]) ==
                               signature(now["text"])):
            before = None
        for part in (before, now):
            if part:
                part.pop("version_id")
        return before, now

    def online(self, celex: str, languages=("EN",)) -> tuple[str, str]:
        """(text, language) from EUR-Lex, or ("", status) with status MISSING (no such language) / ERROR / OFF."""
        if self.fetch_dir is None or not celex:
            return "", "OFF"
        import eurlex_text_fetch as fetch
        statuses = []
        for language in languages:
            text, status = fetch.fetch_text(celex, language, self.fetch_dir)
            self.fetched[f"{language}_{status}"] += 1
            if status == "OK":
                self.failures_in_a_row = 0
                return text, language
            statuses.append(status)
            for attempt in fetch.last_attempts if status == "ERROR" else []:
                self.fetch_errors[attempt[:160]] += 1
        if "ERROR" in statuses:
            self.failures_in_a_row += 1
            if self.failures_in_a_row >= STOP_FETCH_AFTER and not any(
                    key.endswith("_OK") for key in self.fetched):
                print(f"  EUR-Lex could not be reached {STOP_FETCH_AFTER} times in a row: fetching is switched off "
                      f"for this run. Reasons: {dict(self.fetch_errors.most_common(2))}", flush=True)
                self.fetch_dir = None
            return "", "ERROR"
        return "", "MISSING"

    def rows(self, sql, **params):
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params or None)
            return cursor.fetchall()

    def document(self, document_id: str):
        """(versions, {version id: elements}) of one document, EN only."""
        if document_id not in self.cache:
            if len(self.cache) > 200:
                self.cache.clear()
                self.raw.clear()
                self.indexes.clear()
            versions = self.rows(VERSIONS_SQL, document=document_id)
            elements = defaultdict(list)
            raw_elements = self.rows(ELEMENTS_SQL, ids=[v["id"] for v in versions]) if versions else []
            self.raw[document_id] = (versions, raw_elements)
            if versions:
                for row in raw_elements:
                    elements[row["version_id"]].append({
                        "identifier": row["identifier"] or "", "heading": row["heading"],
                        "text": clean(row["body_text"]), "path": row["path"], "depth": row["depth"] or 0})
            self.cache[document_id] = (versions, elements)
        return self.cache[document_id]

    def main_version(self, document_id: str, celex: str | None = None) -> tuple[list[dict], str]:
        """(root elements, source) of the version to read a changing act from (preferred, current, original).
        Without stored text and with fetching on, the act is read from EUR-Lex (source EURLEX_EN)."""
        versions, elements = self.document(document_id)
        if not versions or not any(elements.get(v["id"]) for v in versions):
            text, language = self.online(celex or "")
            if not text:
                return [], "NONE"
            from eurlex_text_fetch import split_elements
            return [{"identifier": e["identifier"], "heading": e["heading"], "text": clean(e["text"]), "path": None,
                     "depth": 0} for e in split_elements(text)], f"EURLEX_{language}"
        best = max(versions, key=lambda v: (bool(v["is_preferred"]), bool(v["is_current"]),
                                            v["version_kind"] == "ORIGINAL", len(elements.get(v["id"], []))))
        items = elements.get(best["id"], [])
        roots = [e for e in items if root_of(e["identifier"]) and
                 re.fullmatch(r"\s*(?:Article|Annex|Artikel|Anhang)(?:\s+[0-9IVXLCDMa-z]+)?\.?\s*", e["identifier"], re.I)]
        return (roots or items), "DATABASE"


# ------------------------------------------------------------------ building the records

def changing_part(rel) -> dict:
    return {"celex": rel["changing_celex"], "title": rel["changing_title"] or "", "date": rel["changing_date"],
            "entry_into_force": rel["entry_into_force"], "application": rel["application"],
            "url": rel["changing_url"]}


def build_records(store: Store, relationships: list[dict], say=print) -> tuple[list[dict], Counter]:
    gaps = Counter()
    records = []
    changes = defaultdict(list)
    ids = [rel["id"] for rel in relationships]
    for start in range(0, len(ids), 2000):
        for row in store.rows(CHANGES_SQL, ids=ids[start:start + 2000]):
            changes[row["relationship_id"]].append(row)
    change_ids = [row["id"] for rows in changes.values() for row in rows]
    reviewed = {}
    for start in range(0, len(change_ids), 2000):
        for row in store.rows(COMPARISONS_SQL, ids=change_ids[start:start + 2000]):
            reviewed[row["change_id"]] = row

    for number, rel in enumerate(relationships, 1):
        if number % 200 == 0:
            say(f"  {number:,}/{len(relationships):,} relationships")
        changing_elements, text_source = store.main_version(rel["changing_document"], rel["changing_celex"]) \
            if rel["relation_type"] != "CORRECTS" else (store.main_version(rel["changing_document"])[0], "DATABASE")
        finals = final_articles(changing_elements)
        base = {"relation": rel["relation_type"], "relationship_id": rel["id"], "status": rel["status"],
                "changing_document_id": rel["changing_document"], "changed_document_id": rel["changed_document"],
                "changing": changing_part(rel),
                "changed": {"celex": rel["changed_celex"], "title": rel["changed_title"] or "", "url": rel["changed_url"]},
                "final_articles": finals, "valid_from": rel["valid_from"], "valid_to": rel["valid_to"],
                "changing_text_source": text_source}
        if not changing_elements and rel["relation_type"] != "CORRECTS":
            gaps["changing_act_without_text"] += 1
        gaps[f"changing_text_{text_source}"] += rel["relation_type"] != "CORRECTS"

        if rel["relation_type"] == "CORRECTS":
            # The database often holds the corrected act's own text under the corrigendum: only real corrigendum
            # text counts. Otherwise the corrigendum is read from EUR-Lex, German first, then English. A corrigendum
            # that exists in neither language only corrects other language versions.
            text, source = " ".join(e["text"] for e in changing_elements), "DATABASE"
            if not is_corrigendum_text(text):
                text, source = store.online(rel["changing_celex"], ("DE", "EN"))
                source = {"DE": "EURLEX_DE", "EN": "EURLEX_EN", "MISSING": "OTHER_LANGUAGES_ONLY"}.get(
                    source, "NOT_FETCHED" if source == "OFF" else "FETCH_ERROR")
            found = corrections_in(text) if text else []
            gaps["corrigenda"] += 1
            gaps[f"corrigenda_text_{source}"] += 1
            gaps["corrigenda_without_parsed_corrections"] += not found
            records.append({**base, "kind": "CORRIGENDUM", "id": record_id("C", rel["id"]), "corrections": found,
                            "instruction": "" if found or not text else clean(text)[:INSTRUCTION_LIMIT],
                            "corrigendum_source": source,
                            "languages": {"EURLEX_DE": "DE", "EURLEX_EN": "EN", "DATABASE": "EN"}.get(source, "")})
            continue

        rows = changes.get(rel["id"], [])
        if not rows:  # act-level only: whole act repealed, application deferred, ...
            forms_ = act_forms(rel["changed_celex"])
            citing = [e for e in changing_elements if cites(e["text"], forms_)]
            text = " ".join(block for e in citing for block in act_blocks(e["text"], forms_))[:INSTRUCTION_LIMIT]
            gaps["act_level_relationships"] += 1
            records.append({**base, "kind": "ACT", "id": record_id("A", rel["id"]), "operation": rel["relation_type"],
                            "instruction": text, "instruction_locator": citing[0]["identifier"] if citing else None,
                            "wording": "INSTRUCTION_ONLY" if text else "NONE"})
            continue

        forms = act_forms(rel["changed_celex"])
        whole_part = " ".join(block for e in changing_elements if cites(e["text"], forms)
                              for block in act_blocks(e["text"], forms))[:INSTRUCTION_LIMIT]
        both_eurlex = (rel.get("changing_source") or "EUR_LEX") == "EUR_LEX" == (rel.get("changed_source") or "EUR_LEX")
        for row in rows:
            locator = row["target_provision"] or " ".join(filter(None, [row["target_locator_type"],
                                                                         row["target_locator_number"]]))
            root = root_of(locator)
            raw = row["raw_annotation"] or {}
            day, day_source = change_day(row, rel)
            operation = (row["operation"] or "OTHER").upper()
            instruction, instruction_locator = instruction_for(changing_elements, rel["changed_celex"], root)
            if root is None:                    # "Chapter III", "Title": the points about that place
                instruction, instruction_locator = place_instruction(changing_elements, rel["changed_celex"], locator)
            if root is None and not instruction and whole_part and not PLACE_WORD.search(locator or ""):
                instruction, instruction_locator = whole_part, None   # no place at all: the whole amending part
            annex = changing_annex(instruction, changing_elements)
            if annex:                           # the amendments or the new wording stand in the changing act's annex
                instruction = f"{instruction} {annex[1]}"[:INSTRUCTION_LIMIT]
            # Old and new wording exactly as the UI shows them: reviewed comparison, else the backend's matcher.
            if row["id"] in reviewed:
                stored = reviewed[row["id"]]
                old, new = clean(stored["old_text"]) or None, clean(stored["new_text"]) or None
                instruction = clean(stored["instruction_text"]) or instruction
                status = "REVIEWED_MAPPING"
            elif str(row.get("resolution_status") or "").upper().startswith("UNRESOLVED"):
                old = new = None
                status = "TARGET_UNRESOLVED"
            elif both_eurlex:
                matched = store.backend_match({**row, "raw_annotation": raw}, rel)
                old, new = clean(matched.get("old_text")) or None, clean(matched.get("new_text")) or None
                status = matched.get("comparison_status") or "NOT_MAPPED"
                backend_instruction = (matched.get("comparison_provenance") or {}).get("instruction_text")
                if backend_instruction:
                    instruction = clean(backend_instruction).lstrip(";,. ")[:INSTRUCTION_LIMIT]
            else:
                old = new = None
                status = "NOT_MAPPED"
            heading, point_context = store.place_context(row, rel) if both_eurlex else (None, None)
            article_before, article_now = store.article_texts(row, rel, day) if both_eurlex else (None, None)
            trimmed = False
            if old and new:
                old, new, trimmed = changed_regions(old, new)
            trimmed = trimmed or len(old or "") > TEXT_LIMIT or len(new or "") > TEXT_LIMIT
            old, new = (old[:TEXT_LIMIT] if old else old), (new[:TEXT_LIMIT] if new else new)
            wording = wording_of(old, new, instruction)
            gaps[f"wording_{wording}"] += 1
            gaps[f"comparison_{status}"] += 1
            gaps["target_not_resolved"] += root is None
            gaps["instruction_not_found"] += not instruction
            gaps["no_effective_date"] += day is None
            records.append({**base, "kind": "PROVISION", "id": record_id("P", row["id"]), "change_id": row["id"],
                            "ordinal": row["ordinal"], "operation": operation, "provision": locator,
                            "root": list(root) if root else None, "target_element_id": row["target_element_id"],
                            "resolution_status": row["resolution_status"], "effective_from": day,
                            "effective_source": day_source, "effective_to": row["effective_to"],
                            "old_text": old, "new_text": new, "comparison_status": status,
                            "heading": heading, "point_context": point_context,
                            "article_before": article_before, "article_now": article_now,
                            "instruction": instruction, "instruction_locator": instruction_locator,
                            "wording": wording, "wording_source": "BACKEND" if status != "NOT_MAPPED" else "NONE",
                            "trimmed": trimmed})
    return records, gaps


def change_day(row, rel) -> tuple:
    """(day, where it comes from): the change's own date first, then the relationship's, then entry into force."""
    raw = row.get("raw_annotation") or {}
    for value, source in ((row.get("effective_from"), "CHANGE"), (_date(raw.get("START_OF_VALIDITY")), "ANNOTATION"),
                          (rel.get("valid_from"), "RELATIONSHIP"), (rel.get("entry_into_force"), "ENTRY_INTO_FORCE")):
        if value:
            return value, source
    return None, "MISSING"


def _date(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


ROLE_WORDS = {"changes_others": "Ändert andere Rechtsakte", "was_changed": "Wurde geändert",
              "was_corrected": "Wurde berichtigt", "corrigendum": "Berichtigung"}


def act_roles(relationships: list[dict]) -> dict[str, dict]:
    """The roles of every act, from ALL operational relationships in the database (not only the exported ones).
    The flags are independent: an act can change others and be changed itself. A corrigendum is not a change
    of the law, so it gives "was_corrected", not "was_changed". "corrigendum" marks corrigendum documents
    (CELEX ...R(01)) and acts that only correct others."""
    makes, changed, corrected = defaultdict(set), set(), set()
    for rel in relationships:
        if rel["relation_type"] not in TEXT_CHANGES or not rel["changing_celex"] or not rel["changed_celex"]:
            continue
        makes[rel["changing_celex"]].add(rel["relation_type"])
        (corrected if rel["relation_type"] == "CORRECTS" else changed).add(rel["changed_celex"])
    roles = {}
    for celex in set(makes) | changed | corrected:
        made = makes.get(celex, set())
        flags = {"changes_others": bool(made - {"CORRECTS"}), "was_changed": celex in changed,
                 "was_corrected": celex in corrected,
                 "corrigendum": bool(re.search(r"R\(\d+\)$", celex)) or made == {"CORRECTS"}}
        roles[celex] = {**flags, "labels": [ROLE_WORDS[k] for k, v in flags.items() if v]}
    return roles


def in_scope(rel: dict, relevant: set[str], since: int | None, celex: set[str]) -> bool:
    if celex:
        return rel["changing_celex"] in celex or rel["changed_celex"] in celex
    if rel["changing_celex"] not in relevant and rel["changed_celex"] not in relevant:
        return False
    year = year_of(rel["changing_celex"], rel["changing_date"])
    return since is None or (year is not None and year >= since)


def plain(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    parser.add_argument("--classification", type=Path, default=ROOT / "runs/eurlex_classification_v5")
    parser.add_argument("--out", type=Path, default=ROOT / "runs/eurlex_changes_export_v5")
    parser.add_argument("--since", type=int, default=2015, help="Changing acts from this year on (0 = all).")
    parser.add_argument("--celex", nargs="+", default=[], help="Only relationships of these acts (any year).")
    parser.add_argument("--count-only", action="store_true")
    parser.add_argument("--backend-dir", type=Path, default=os.environ.get("LR_BACKEND_DIR"),
                        help="The backend folder (holds app\\services\\eurlex_wording.py). Its wording matcher is used, "
                             "so old and new text are the same as in the UI. Or set LR_BACKEND_DIR.")
    parser.add_argument("--fetch-missing", action="store_true",
                        help="Read corrigenda and changing acts without text in the database from eur-lex.europa.eu.")
    parser.add_argument("--fetch-cache", type=Path, default=ROOT / "runs/eurlex_fetch_cache",
                        help="Where fetched EUR-Lex texts are kept (a second run does not fetch them again).")
    args = parser.parse_args(argv)
    try:
        from dotenv import dotenv_values
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        parser.exit(1, f"Missing package: {exc}\n")
    if not args.count_only:
        if not args.backend_dir:
            parser.exit(1, "Give the backend folder: --backend-dir <folder of legal_radar_backend>\n")
        try:
            from backend_wording import BackendWording
            wording = BackendWording.load(args.backend_dir)
        except (OSError, ImportError) as exc:
            parser.exit(1, f"Backend wording matcher not usable: {exc}\n")
    else:
        wording = None
    results_file = args.classification / "results.json"
    if not results_file.is_file() and not args.celex:
        parser.exit(1, f"Classification results not found: {results_file}\n")
    relevant = set(json.loads(results_file.read_text(encoding="utf-8"))) if results_file.is_file() else set()
    values = dotenv_values(args.env) if args.env.exists() else {}
    url = os.environ.get("DATABASE_URL") or values.get("DATABASE_URL")
    if not url:
        parser.exit(1, "DATABASE_URL is missing from the .env\n")
    for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://"):
        url = url.replace(prefix, "postgresql://", 1)
    out = args.out
    if not args.count_only and (out / "records.jsonl").exists():
        parser.exit(1, f"{out} already holds an export. Use a new --out folder.\n")
    celex = {value.strip().upper() for value in args.celex}
    since = args.since or None

    try:
        with psycopg.connect(url, row_factory=dict_row, connect_timeout=20) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            connection.execute("SET LOCAL statement_timeout = '30min'")
            store = Store(connection, args.fetch_cache if args.fetch_missing else None, wording)
            everything = store.rows(RELATIONSHIPS_SQL)
            chosen = [rel for rel in everything if in_scope(rel, relevant, since, celex)]
            text_changes = [rel for rel in chosen if rel["relation_type"] in TEXT_CHANGES]
            links = [rel for rel in chosen if rel["relation_type"] in LINKS_ONLY]
            print(f"{len(everything):,} operational relationships in the database. In scope: {len(chosen):,} "
                  f"({len(text_changes):,} that change text or application, {len(links):,} links only).", flush=True)
            print("By type:", dict(Counter(rel["relation_type"] for rel in chosen).most_common()), flush=True)
            if args.count_only:
                return 0
            records, gaps = build_records(store, text_changes)
            roles = act_roles(everything)
    except Exception as exc:  # noqa: BLE001 - never print a driver message that may hold the connection string
        detail = str(exc).splitlines()[0][:200] if not isinstance(exc, OSError) else ""
        parser.exit(1, f"Export failed ({type(exc).__name__}). {detail}\n")

    out.mkdir(parents=True, exist_ok=True)
    temp = out / "records.jsonl.tmp"
    with temp.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, default=plain) + "\n")
    temp.replace(out / "records.jsonl")
    link_rows = [{"relation": rel["relation_type"], "changing": changing_part(rel),
                  "changed": {"celex": rel["changed_celex"], "title": rel["changed_title"] or ""}} for rel in links]
    (out / "roles.json").write_text(json.dumps(roles, ensure_ascii=False, indent=0), encoding="utf-8")
    (out / "links.json").write_text(json.dumps(link_rows, ensure_ascii=False, indent=1, default=plain), encoding="utf-8")
    summary = {"format": FORMAT, "created_at": datetime.now(timezone.utc).isoformat(), "since": since,
               "celex": sorted(celex), "relevant_acts": len(relevant), "relationships_in_scope": len(chosen),
               "records": len(records), "by_kind": dict(Counter(r["kind"] for r in records)),
               "by_relation": dict(Counter(r["relation"] for r in records)),
               "by_operation": dict(Counter(r.get("operation") for r in records if r["kind"] == "PROVISION")),
               "gaps": dict(gaps), "links_only": len(links), "fetched_from_eurlex": dict(store.fetched),
               "backend_wording": {"file": str(wording.path), "sha256": wording.sha256},
               "roles": dict(Counter(" + ".join(r["labels"]) for r in roles.values())),
               "fetch_errors": dict(store.fetch_errors.most_common(5))}
    (out / "export_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("records", "by_kind", "gaps", "fetched_from_eurlex", "fetch_errors")},
                     ensure_ascii=False, indent=1))
    print(f"Saved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
