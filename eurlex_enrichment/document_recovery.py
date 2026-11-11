"""Recover article and annex targets from a version stored as full text."""

from dataclasses import dataclass
import re

from .pdf_source import pdf_text
from .xhtml import NS, tag, text


CORRIGENDUM_ID = re.compile(r"R\(\d+\)$")
ARTICLE_HEADING = {
    "EN": re.compile(r"Article ([1-9]\d*)|(?:Sole|Single) Article", re.I),
    "DE": re.compile(r"Artikel ([1-9]\d*)|Einziger Artikel", re.I),
}
ANNEX_HEADING = {
    "EN": re.compile(r"Annex(?: ([IVXLCDM]+[A-Z]?|\d+[A-Z]?))?", re.I),
    "DE": re.compile(r"Anhang(?: ([IVXLCDM]+[A-Z]?|\d+[A-Z]?))?", re.I),
}
AMBIGUOUS_ANNEX = re.compile(r"(?:ANNEX|ANHANG) (?:[IVXLCDM]+[A-Z]?|\d+[A-Z]?)\b")
FUSED_HEADING = re.compile(r"[.!?]\s+(?:Article|Artikel) ([1-9]\d*)$")


@dataclass(frozen=True)
class Line:
    text: str
    source: dict
    can_be_heading: bool = True


def html_lines(xhtml):
    paths = ".//h:p|.//h:h1|.//h:h2|.//h:h3|.//h:h4|.//h:h5|.//h:h6|.//h:table"
    bodies = xhtml.root.xpath("./h:body", namespaces=NS)
    lines = []
    for body in bodies:
        blocks = body.xpath(paths, namespaces=NS)
        # Use block extraction only when it accounts for the body text. This
        # avoids silently dropping text held in a div or alongside a table.
        top = set(blocks)
        for node in blocks:
            if any(parent in top for parent in node.iterancestors()):
                continue
            if body_text := text(node):
                lines.append(Line(body_text, {
                    "kind": "HTML_BLOCK", "xpath": node.getroottree().getpath(node),
                }, tag(node) != "table"))
        actual = "".join(text(body).split())
        if actual == "".join("".join(line.text.split()) for line in lines):
            return lines, text(body)
    return [], "\n".join(text(body) for body in bodies)


def stored_lines(rows):
    for row in rows:
        for number, line in enumerate((row.get("body_text") or "").splitlines(), 1):
            yield Line(line, {"kind": "POSTGRESQL_LEGAL_ELEMENT", "element_id": row["id"], "line": number})


def split_provisions(lines, language):
    markers = []
    possible_fused_articles = set()
    for index, line in enumerate(lines):
        if not line.can_be_heading:
            continue
        label = " ".join(line.text.strip().split())
        if article := ARTICLE_HEADING[language].fullmatch(label):
            markers.append((index, "ARTICLE", article[1] or "1", label.rstrip(".")))
        elif annex := ANNEX_HEADING[language].fullmatch(label):
            markers.append((index, "ANNEX", (annex[1] or "1").upper(), label))
        elif AMBIGUOUS_ANNEX.match(label):
            return []
        elif fused := FUSED_HEADING.search(label):
            # A column may end in prose just as another column starts a
            # heading. An unaccounted heading makes this split ambiguous.
            possible_fused_articles.add(int(fused[1]))
    articles = [int(number) for _, kind, number, _ in markers if kind == "ARTICLE"]
    annexes = [number for _, kind, number, _ in markers if kind == "ANNEX"]
    kinds = [kind for _, kind, _, _ in markers]
    if (not articles or articles != list(range(1, len(articles) + 1))
            or possible_fused_articles.difference(articles)
            or len(set(annexes)) != len(annexes)
            or kinds != ["ARTICLE"] * len(articles) + ["ANNEX"] * len(annexes)):
        return []
    provisions = []
    for offset, (start, kind, number, label) in enumerate(markers):
        end = markers[offset + 1][0] if offset + 1 < len(markers) else len(lines)
        body = "\n".join(line.text for line in lines[start + 1:end]).strip()
        if not any(character.isalpha() for character in body):
            return []
        provisions.append((kind, number, label, body, lines[start].source, lines[end - 1].source))
    return provisions


def recover_document(data, raw, xhtml):
    rows = [row for row in data["legal_elements"] if row["element_type"] == "DOCUMENT_TEXT"]
    if not rows:
        return [], None
    celex = data["celex"]
    language = data["version"]["language"]
    db_lines = list(stored_lines(rows))
    body = "\n".join(line.text for line in db_lines).strip()
    correction = bool(CORRIGENDUM_ID.search(celex))
    additions, source_text = [], ""
    if xhtml is None:
        locations = [] if correction and body else pdf_text(raw)
        native = [Line(value, {"kind": "PDF_TEXT", "page": page, "line": number})
                  for page, number, value in locations]
        source_text = "\n".join(line.text for line in native).strip()
    else:
        native, source_text = html_lines(xhtml) if not (correction and body) else ([], "")

    ordinal = max(row["ordinal"] for row in data["legal_elements"])
    if not correction:
        native_parts = split_provisions(native, language)
        db_parts = split_provisions(db_lines, language)
        if not (native_parts and db_parts and [p[:2] for p in native_parts] != [p[:2] for p in db_parts]):
            for kind, number, label, value, start, end in native_parts or db_parts:
                ordinal += 1
                additions.append({
                    "id": f"{celex}:recovered:{kind.lower()}:{number}",
                    "element_type": kind, "identifier": label, "number": number,
                    "parent_id": None, "ordinal": ordinal, "path": f"/{kind.lower()}/{number}",
                    "body_text": value, "details": {"aggregate_body": True},
                    "recovery_source": {"start": start, "end": end},
                })
            if additions:
                return additions, "HEADING_RECOVERY"

    value = body or source_text
    if not any(character.isalpha() for character in value):
        return [], None
    return [{
        "id": f"{celex}:recovered:document-text",
        "element_type": "DOCUMENT_TEXT",
        "identifier": "Corrigendum (full text)" if correction else "Document (full text)",
        "number": None, "parent_id": None, "ordinal": ordinal + 1, "path": "/document-text/recovered",
        "body_text": value, "details": {"aggregate_body": True},
        "recovery_source": {"kind": "POSTGRESQL_LEGAL_ELEMENT" if body else "SOURCE_SNAPSHOT",
                            "element_ids": [row["id"] for row in rows] if body else []},
    }], "CORRIGENDUM_TEXT" if correction else "DOCUMENT_TEXT"
