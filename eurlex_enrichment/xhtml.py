"""Extract annex blocks, real table grids, and footnote evidence from XHTML."""

import re
from collections import defaultdict

from lxml import etree

NS = {"h": "http://www.w3.org/1999/xhtml"}
PART = re.compile(r"^Part\s+([0-9]+[a-z]?|[IVXLCDM]+)\b", re.I)
TABLE = re.compile(r"^Table\s+([0-9]+[a-z]?)\b", re.I)
BAT = re.compile(r"^(\d{1,3})\.\s+.{0,600}?\bBAT (?:is|are)\b", re.I)
SECTION = re.compile(r"^\d+(?:\.\d+)+\b")
BLOCK_TAGS = {"p", "div", "li", "tr", "td", "th", "figure", "br"}
ANNEX_LABEL = re.compile(r"ANNEX(?: [A-Z0-9]+)?", re.I)
HTML_START = re.compile(r"(?:<!doctype\s+html\b|<html(?:\s|>))", re.I)
LEADING_MARKUP = re.compile(r"^(?:\s|<!--.*?-->|<\?xml\s.*?\?>)*", re.I | re.S)


class SourceMarkupError(ValueError):
    """The verified snapshot cannot be used as legal HTML."""

    def __init__(self, payload_type, message):
        super().__init__(message)
        self.payload_type = payload_type


class AnnexLocatorError(ValueError):
    """The source does not identify a unique, bounded annex block."""


def payload_type(raw):
    if raw.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        prefix = raw[:65536].decode("utf-32", errors="replace")
    elif raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        prefix = raw[:65536].decode("utf-16", errors="replace")
    else:
        prefix = raw[:65536].decode("utf-8-sig", errors="replace")
    prefix = prefix.lstrip()
    if not prefix:
        return "EMPTY_OR_WHITESPACE_PREFIX"
    if prefix.startswith("%PDF-"):
        return "PDF"
    if raw.startswith(b"\x1f\x8b"):
        return "GZIP"
    if raw.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return "ZIP"
    if prefix.startswith(("{", "[")):
        return "JSON_LIKE"
    if HTML_START.match(prefix, LEADING_MARKUP.match(prefix).end()):
        return "HTML"
    if prefix.startswith("<"):
        return "OTHER_MARKUP"
    return "NON_MARKUP"


def parse_document(raw):
    parser = etree.XMLParser(no_network=True, resolve_entities=False, load_dtd=False)
    try:
        root = etree.fromstring(raw, parser)
        if root.tag == f"{{{NS['h']}}}html":
            return root, "XHTML_XML"
        if root.tag != "html":
            raise SourceMarkupError(payload_type(raw), "Verified snapshot has a non-HTML root.")
        mode = "HTML_XML"
    except etree.XMLSyntaxError as exc:
        kind = payload_type(raw)
        if kind != "HTML":
            raise SourceMarkupError(kind, "Verified snapshot could not be parsed as legal HTML.") from exc
        encoding = None
        prefix = raw[:65536]
        if not re.search(rb"<meta\b[^>]*charset\s*=|<\?xml\b[^?]*encoding\s*=", prefix, re.I):
            try:
                raw.decode("utf-8")
            except UnicodeDecodeError:
                pass  # Let libxml detect the source encoding.
            else:
                encoding = "UTF-8"
        html_parser = etree.HTMLParser(encoding=encoding, no_network=True, recover=True)
        try:
            root = etree.fromstring(raw, html_parser)
        except etree.XMLSyntaxError as recovery_error:
            raise SourceMarkupError(kind, "Legacy HTML parser failed.") from recovery_error
        if html_parser.error_log.filter_from_level(etree.ErrorLevels.FATAL):
            raise SourceMarkupError(kind, "Legacy HTML parser reported a fatal error.")
        mode = "HTML_RECOVERY"

    if root is None or tag(root) != "html":
        raise SourceMarkupError("HTML", "Legacy HTML has no document root.")
    for node in root.iter():
        if isinstance(node.tag, str) and not node.tag.startswith("{"):
            # The existing extraction paths use XHTML names for HTML elements.
            # Leave prefixed names alone, including unknown XML extensions.
            if ":" not in node.tag:
                node.tag = f"{{{NS['h']}}}{node.tag}"
    bodies = root.xpath("./h:body", namespaces=NS)
    if len(bodies) != 1 or not text(bodies[0]):
        raise SourceMarkupError("HTML", "Legacy HTML has no nonempty body.")
    return root, mode


def tag(node):
    return node.tag.rsplit("}", 1)[-1] if isinstance(node.tag, str) else ""


def classes(node):
    return (node.get("class") or "").split()


def text(node, omit=()):
    """Keep inline symbols attached and insert separators at block boundaries."""
    def pieces(element):
        if element in omit:
            yield " "
            return
        if tag(element) in BLOCK_TAGS:
            yield " "
        yield element.text or ""
        for child in element:
            if isinstance(child.tag, str):
                yield from pieces(child)
            yield child.tail or ""
        if tag(element) in BLOCK_TAGS:
            yield " "
    return " ".join("".join(pieces(node)).split())


def locator(node):
    return {"id": node.get("id"), "xpath": node.getroottree().getpath(node)}


def fragment_ids(node):
    return list(dict.fromkeys(
        link[1:] for link in node.xpath(".//h:a/@href", namespaces=NS)
        if link.startswith("#")
    ))


def table_grid(table):
    rows = []
    # XHTML row spans are scoped to their row group.
    groups = defaultdict(list)
    for row in table.xpath("./h:tr|./h:thead/h:tr|./h:tbody/h:tr|./h:tfoot/h:tr", namespaces=NS):
        groups[row.getparent()].append(row)
    for group_number, (group, group_rows) in enumerate(groups.items(), 1):
        occupied_until = {}
        for row_index, row in enumerate(group_rows, 1):
            column = 1
            cells = []
            for cell in row.xpath("./h:td|./h:th", namespaces=NS):
                colspan = int(cell.get("colspan", "1"))
                rowspan = int(cell.get("rowspan", "1"))
                if colspan < 1 or rowspan < 0:
                    raise ValueError("Invalid colspan or rowspan in XHTML")
                effective_rowspan = rowspan or len(group_rows) - row_index + 1
                while any(occupied_until.get(c, 0) >= row_index
                          for c in range(column, column + colspan)):
                    column += 1
                for c in range(column, column + colspan):
                    occupied_until[c] = row_index + effective_rowspan - 1
                cells.append({
                    "column": column, "text": text(cell),
                    "header": tag(cell) == "th" or any(
                        "oj-tbl-hdr" in classes(node) for node in cell.iterdescendants()
                    ),
                    "rowspan": rowspan, "colspan": colspan,
                    "source": locator(cell),
                    "links": [{"text": text(a), "href": a.get("href")}
                              for a in cell.xpath(".//h:a[@href]", namespaces=NS)],
                    "footnote_ids": [key for key in fragment_ids(cell) if key.startswith("ntr")],
                    "image_sources": [locator(image) for image in cell.xpath(".//h:img", namespaces=NS)],
                })
                column += colspan
            rows.append({
                "row": len(rows) + 1, "row_group": group_number,
                "group_type": tag(group), "cells": cells,
            })
    return rows


class XHTML:
    def __init__(self, raw):
        self.root, self.parser_mode = parse_document(raw)
        self.annex_locator_recoveries = []
        self.used_annex_roots = set()
        self.used_annex_blocks = set()
        self.by_id = defaultdict(list)
        for node in self.root.xpath("//*[@id]"):
            self.by_id[node.get("id")].append(node)
        self.notes = {}
        for note in self.root.xpath("//h:p", namespaces=NS):
            if "oj-note" not in classes(note):
                continue
            for note_id in note.xpath(".//h:a[starts-with(@id,'ntr')]/@id", namespaces=NS):
                parts = [text(note)]
                for sibling in note.itersiblings():
                    if "oj-note" not in classes(sibling) or sibling.xpath(".//h:a[@id]", namespaces=NS):
                        break
                    parts.append(text(sibling))
                self.notes[note_id] = {"text": "\n".join(parts), "source": locator(note)}

    def node(self, source_id):
        matches = self.by_id.get(source_id, [])
        return matches[0] if len(matches) == 1 else None

    def notes_for(self, source_id):
        node = self.node(source_id)
        return [] if node is None else [key for key in fragment_ids(node) if key.startswith("ntr")]

    def document_context(self):
        preamble = [
            child for node in self.root.xpath("//*[starts-with(@id,'pbl_')]")
            for child in node if not (child.get("id") or "").startswith("rct_")
            and isinstance(child.tag, str) and text(child)
        ]
        closing = self.root.xpath(
            "//*[starts-with(@id,'fnp_') and not(ancestor::*[starts-with(@id,'fnp_')])]"
        )
        def blocks(nodes):
            return [{"text": text(node), "source": locator(node),
                     "footnote_ids": [key for key in fragment_ids(node) if key.startswith("ntr")]}
                    for node in nodes]
        return {"preamble_before_recitals": blocks(preamble), "closing": blocks(closing)}

    def annex_root(self, element):
        source_id = element.get("source_locator")
        root = self.node(source_id)
        if root is None or tag(root) not in {"div", "section", "article"} or not len(root):
            label = " ".join(element["identifier"].upper().split())
            candidates = []
            if ANNEX_LABEL.fullmatch(label):
                for container in self.root.xpath("//h:div|//h:section|//h:article", namespaces=NS):
                    if not (
                        set(classes(container)) & {"eli-container", "annex"}
                        or re.match(r"^(?:anx|annex)(?:[_-]|$)", container.get("id", ""), re.I)
                    ):
                        continue
                    blocks = (child for child in container if isinstance(child.tag, str) and text(child))
                    first = next(blocks, None)
                    if first is None or next(blocks, None) is None:
                        continue
                    headings = (
                        node for node in container.iterdescendants()
                        if "oj-doc-ti" in classes(node) or tag(node) in {"h1", "h2", "h3", "h4", "h5", "h6"}
                    )
                    annex_headings = [heading for heading in headings if ANNEX_LABEL.fullmatch(text(heading))]
                    if annex_headings == [first] and text(first).upper() == label:
                        candidates.append(container)
            if len(candidates) != 1:
                raise AnnexLocatorError(f"Annex has no unique XHTML locator: {element['identifier']}")
            root = candidates[0]
            self.annex_locator_recoveries.append({
                "annex_id": element["id"], "original_locator": source_id, "source": locator(root),
            })
        if root in self.used_annex_roots:
            raise AnnexLocatorError(f"XHTML block maps to more than one annex: {element['identifier']}")
        self.used_annex_roots.add(root)
        return root

    def legacy_annex_blocks(self, element, body):
        """Verify a flat annex range against the stored body of the same annex."""
        label = " ".join(element["identifier"].upper().split())
        expected = " ".join(body.split())
        candidates = []
        for heading in self.root.xpath("//h:p|//h:h1|//h:h2|//h:h3|//h:h4|//h:h5|//h:h6", namespaces=NS):
            if text(heading).upper() != label:
                continue
            if any(tag(parent) in {"table", "td", "th"} for parent in heading.iterancestors()):
                continue
            parts, blocks = [], [heading]
            for block in heading.itersiblings():
                if not isinstance(block.tag, str):
                    continue
                content = text(block)
                if ANNEX_LABEL.fullmatch(content) or re.fullmatch(r"Article \d+[a-z]?", content, re.I):
                    break
                blocks.append(block)
                if content:
                    parts.append(content)
            whole = " ".join(parts)
            if whole and expected in {whole, f"{text(heading)} {whole}"}:
                candidates.append((heading, blocks))
        if len(candidates) != 1:
            raise AnnexLocatorError(f"Annex has no unique XHTML locator: {element['identifier']}")
        root, blocks = candidates[0]
        self.annex_locator_recoveries.append({
            "annex_id": element["id"], "original_locator": element.get("source_locator"),
            "source": locator(root), "last_block": locator(blocks[-1]), "match": "DATABASE_BODY",
        })
        return root, blocks

    def annex(self, element, database_body=None):
        try:
            root = self.annex_root(element)
            blocks = list(root)
        except AnnexLocatorError:
            if not database_body:
                raise
            root, blocks = self.legacy_annex_blocks(element, database_body)
        if any(block in self.used_annex_blocks or any(
            parent in self.used_annex_blocks for parent in block.iterancestors()
        ) or any(child in self.used_annex_blocks for child in block.iterdescendants()) for block in blocks):
            raise AnnexLocatorError(f"XHTML annex overlaps another annex: {element['identifier']}")
        self.used_annex_blocks.update(blocks)
        annex_id = element["id"]
        segments, tables, images = [], [], []
        part = None
        headings, captions = [], []
        bat = None

        def segment(kind, label, source):
            value = {
                "id": f"{annex_id}:{kind.lower()}:{len(segments) + 1}",
                "kind": kind, "label": label, "annex_id": annex_id,
                "part": part, "headings": headings[:], "source": locator(source),
                "blocks": [], "table_ids": [], "image_ids": [],
            }
            segments.append(value)
            return value

        current = segment("SECTION", "Annex opening and scope", root)
        for block in blocks:
            if not isinstance(block.tag, str):
                continue
            content = text(block)
            css = classes(block)
            if "oj-ti-tbl" in css:
                captions.append({"text": content, "source": locator(block)})
            elif "oj-doc-ti" in css or any(c.startswith("oj-ti-grseq") for c in css):
                match = PART.match(content)
                if match:
                    part = match[1].upper()
                structural = match or SECTION.match(content) or bat is None
                if structural:
                    headings = (headings + [content])[-3:]
                    current = segment("SECTION", content, block)
                    bat = None
            elif tag(block) == "p":
                match = BAT.match(content)
                if match:
                    bat = segment("BAT", f"BAT {match[1]}", block)
                    current = bat

            candidates = []
            if tag(block) == "table":
                candidates.append(block)
            candidates.extend(block.xpath(".//h:table", namespaces=NS))
            data_tables = [table for table in candidates
                           if "oj-table" in classes(table) or table.xpath("./h:thead", namespaces=NS)]
            for table in data_tables:
                caption = captions[:] if len(data_tables) == 1 else []
                published = next(
                    (m[1].upper() for item in caption if (m := TABLE.match(item["text"]))), None
                )
                row_grid = table_grid(table)
                table_id = f"{annex_id}:table:{len(tables) + 1}"
                cells = [cell for row in row_grid for cell in row["cells"]]
                tables.append({
                    "id": table_id, "annex_id": annex_id, "part": part,
                    "published_number": published, "caption": caption,
                    "section_id": current["id"], "section_label": current["label"],
                    "source": locator(table), "rows": row_grid,
                    "_source_text_keys": [text(table), " ".join(" ".join(table.itertext()).split())],
                    "footnote_ids": list(dict.fromkeys(
                        note for cell in cells for note in cell["footnote_ids"] if note.startswith("ntr")
                    )),
                })
                current["table_ids"].append(table_id)
                current["blocks"].append({"table_id": table_id})
            for image in block.xpath(".//h:img", namespaces=NS):
                image_id = f"{annex_id}:image:{len(images) + 1}"
                images.append({
                    "id": image_id, "section_id": current["id"], "source": locator(image),
                    "alt": image.get("alt"), "src": image.get("src"),
                    "review_status": "VERIFY_FORMULA_AGAINST_PDF"
                    if (image.get("alt") or "").lower() == "formula" else "IMAGE_ONLY",
                })
                current["image_ids"].append(image_id)
                current["blocks"].append({"image_id": image_id})
            non_table_text = text(block, set(data_tables)) if data_tables else content
            if non_table_text:
                current["blocks"].append({
                    "text": non_table_text, "source": locator(block),
                    "footnote_ids": [key for key in fragment_ids(block) if key.startswith("ntr")],
                })
            if (content and "oj-ti-tbl" not in css) or data_tables:
                captions = []

        return segments, tables, images
