"""Repair provision boundaries and text that the stored parse got wrong.

Three repairs, all without a model:

1. False headings. A sentence that begins with "Annex II to Directive ..." or
   "Article 15 of Directive ..." was stored as a new annex or article. The real
   provision then holds only its heading and its text sits under a wrong label.
   merge_false_headings() glues such rows back onto the provision before them.
2. Glyph codes. Some PDF text is stored as "/C67/C79/C85..." (one code per
   character). decode_glyph_codes() turns it back into text.
3. One-block text. inline_provisions() cuts a text without line breaks into
   articles and annexes at headings that follow the end of a sentence.
"""

import re

PROVISION_KINDS = {"ARTICLE", "ANNEX"}
NUMBER = r"(?:[IVXLCDM]+[A-Za-z]?|\d+[A-Za-z]{0,2}|[A-Z])"
PURE_ARTICLE = re.compile(r"(?:Article|Artikel)\s+\d+[a-z]{0,2}\.?|(?:Sole|Single) Article|Einziger Artikel", re.I)
PURE_ANNEX = re.compile(rf"(?:ANNEX|Annex|ANHANG|Anhang)(?:\s+{NUMBER})?")
LABEL_START = re.compile(rf"(?:(Articles?|Artikel)|(Annex(?:es)?|Anhang|Anhänge))\b\.?(?:\s*{NUMBER}\b)?", re.I)
# What follows a label inside a sentence or a citation, never after a heading.
CITATION_TAIL = re.compile(
    r"(?:[(,;:]|\.(?=\s*\S)|\s+[-–—]\s|\s+(?:of|to|and|or|shall|is|are|was|were|may|must|in|on|for|by|as|point|"
    r"points|paragraph|paragraphs|section|part|thereof|von|der|des|und|bis|zu)\b)", re.I)
CITED_LABEL = re.compile(r"(?:Articles?|Annex(?:es)?|Artikel|Anhang)\b")
DOT_LEADER = re.compile(r"\.{5,}")


def squash(text):
    return " ".join((text or "").split())


def is_false_heading(row):
    """True when a stored ARTICLE or ANNEX row starts inside a sentence or a citation."""
    if row.get("element_type") not in PROVISION_KINDS or row.get("parent_id") is not None:
        return False
    heading = squash(row.get("heading"))
    line = heading or squash(row.get("body_text"))[:240]
    start = LABEL_START.match(line)
    if not start or bool(start[1]) != (row["element_type"] == "ARTICLE"):
        return False  # The row does not begin with its own kind of label, so no heading was mistaken.
    if PURE_ARTICLE.fullmatch(line) or PURE_ANNEX.fullmatch(line):
        return False
    label, rest = start[0], line[start.end():]
    word = label.split()[0]
    if word.isupper() and start[2]:
        return False  # Sentences write "Annex"; "ANNEX II WASTE ACCEPTANCE CRITERIA" is a heading.
    if re.match(r"(?:Articles|Annexes|Anhänge)\b", label, re.I) or word.endswith("."):
        return True  # "Annexes I and II to ...", "Annex. The Member State ..."
    if CITATION_TAIL.match(rest):
        return True  # "Article 15 of Directive ...", "Article 6(2) ...", "Annex II to ..."
    if not heading:
        return False  # Without the stored first line, the text after the label may be the provision itself.
    title = rest.strip()
    if DOT_LEADER.search(line) or CITED_LABEL.match(title):
        return True  # A contents line, or a correlation-table row such as "Article 44 Article 33".
    # "Article 3 Scope" is a heading with its title. A long line or one that reads on is a sentence.
    return bool(title) and not (title[:1].isupper() and len(title) <= 120
                                and not re.search(r"\bshall\b|[.;]$", title))


def merge_false_headings(rows):
    """Glue false headings onto the provision before them. Returns (rows, merged labels by provision)."""
    ordered = sorted(rows, key=lambda row: row["ordinal"])
    top = [row for row in ordered if row.get("element_type") in PROVISION_KINDS and row.get("parent_id") is None]
    false = [row for row in top if is_false_heading(row)]
    if not false or len(false) == len(top):
        return rows, {}
    false_ids = {row["id"] for row in false}
    owner, new_parent, merged = None, {}, {}
    texts = {}
    for row in top:
        if row["id"] not in false_ids:
            owner = row
            texts[row["id"]] = [(row.get("body_text") or "").strip()]
            continue
        if owner is None:
            false_ids.discard(row["id"])  # Nothing before it to carry the text; leave the row as stored.
            continue
        new_parent[row["id"]] = owner["id"]
        merged.setdefault(owner["id"], []).append(squash(row.get("heading")) or row["identifier"])
        piece = (row.get("body_text") or "").strip()
        known = squash(" ".join(texts[owner["id"]]))
        if piece and squash(piece) not in known:
            texts[owner["id"]].append(piece)
    if not new_parent:
        return rows, {}
    repaired = []
    for row in ordered:
        if row["id"] in new_parent:
            continue
        row = dict(row)
        if row["id"] in merged:
            row["body_text"] = "\n".join(piece for piece in texts[row["id"]] if piece)
            row["details"] = {**(row.get("details") or {}), "aggregate_body": True}
        if row.get("parent_id") in new_parent:
            row["parent_id"] = new_parent[row["parent_id"]]
        repaired.append(row)
    # A child must come after its parent. Children of a merged row already do: the owner precedes the row.
    return repaired, {next(r["identifier"] for r in repaired if r["id"] == key): labels
                      for key, labels in merged.items()}


GLYPH_CODE = re.compile(r"/C(\d{1,3})(?!\d)")


def decode_glyph_codes(text):
    """Turn "/C67/C79/C85" into "COU". Text without such codes is returned unchanged."""
    if not text or len(GLYPH_CODE.findall(text[:4000])) < 20:
        return text

    def character(match):
        code = int(match[1])
        if code > 255:
            return match[0]
        return bytes([code]).decode("cp1252", "replace") if code >= 128 else chr(code)

    return GLYPH_CODE.sub(character, text)


# A heading inside running text: after the end of a sentence or capitals, before a title or paragraph number.
INLINE_ARTICLE = re.compile(r"(?<![A-Za-z])Article (\d+)([a-z]?)(?=\s+[A-Z0-9‘“▼—(])")
INLINE_ANNEX = re.compile(r"(?<![A-Za-z])ANNEX(?: ([IVXLCDM]+|\d+))?(?=\s+[A-Z0-9‘“▼(])")
BEFORE_HEADING = re.compile(r"(?:[.;:!?)’”»◄]|[A-Z]{2}|[▼►][A-Z]\d*|—)\s*$")
MAX_NUMBER_GAP = 10


def inline_provisions(text):
    """Cut one block of text into provisions. Returns [(kind, number, label, body, start, end)] or []."""
    text = text or ""
    articles, last = [], (0, "")
    for match in INLINE_ARTICLE.finditer(text):
        key = (int(match[1]), match[2])
        if not BEFORE_HEADING.search(text[max(0, match.start() - 12):match.start()]):
            continue
        if not articles and key != (1, ""):
            continue
        if key <= last or key[0] - last[0] > MAX_NUMBER_GAP:
            continue
        articles.append((match.start(), match.end(), "ARTICLE", f"{key[0]}{key[1]}", f"Article {key[0]}{key[1]}"))
        last = key
    if len(articles) < 3:
        return []
    annexes, seen = [], set()
    for match in INLINE_ANNEX.finditer(text, articles[-1][1]):
        number = (match[1] or "1").upper()
        if number in seen:
            continue
        seen.add(number)
        annexes.append((match.start(), match.end(), "ANNEX", number, "Annex" + (f" {match[1]}" if match[1] else "")))
    marks = articles + annexes
    provisions = []
    for index, (start, end, kind, number, label) in enumerate(marks):
        stop = marks[index + 1][0] if index + 1 < len(marks) else len(text)
        body = text[end:stop].strip()
        if any(character.isalpha() for character in body):
            provisions.append((kind, number, label, body,
                               {"kind": "INLINE_TEXT", "offset": start}, {"kind": "INLINE_TEXT", "offset": stop}))
    return provisions
