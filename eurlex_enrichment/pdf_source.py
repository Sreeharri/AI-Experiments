"""Recover page-local annex table grids from the pinned PDF."""

from bisect import bisect_right
from collections import defaultdict
from io import BytesIO
import re
import unicodedata


ANNEX_HEADING = re.compile(r"ANNEX(?:\s+[A-Z0-9]+)?", re.I)
ARTICLE_HEADING = re.compile(r"ARTICLE\s+\d+[A-Z]?", re.I)


def compact(text):
    """Ignore layout whitespace while preserving punctuation and symbols."""
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def heading_index(markers, annexes):
    labels = {key: " ".join(label.upper().split()) for key, (label, _) in annexes.items()}
    if len(labels) == 1 and next(iter(labels.values())) == "ANNEX 1":
        if not any(label == "ANNEX 1" for _, label in markers):
            labels = dict.fromkeys(labels, "ANNEX")
    if not labels or len(set(labels.values())) != len(labels):
        return [], []
    ids = {label: key for key, label in labels.items()}
    known = [label for _, label in markers if label in ids]
    if known != list(labels.values()):
        return [], []
    positions = [position for position, _ in markers]
    return positions, [ids.get(label) for _, label in markers]


def table_owner(grid, body_by_annex, markers, owners):
    position = (grid["source"]["page"], grid["source"]["bbox"][1])
    index = bisect_right(markers, position) - 1
    if index >= 0:
        owner = owners[index]
        bottom = (position[0], grid["source"]["bbox"][3])
        if index + 1 < len(markers) and markers[index + 1] <= bottom:
            return None
        if owner:
            # Confirm that the grid's substantial text appears in the stored
            # annex as well. Heading position alone can include layout boxes.
            anchors = {compact(cell["text"]) for row in grid["rows"] for cell in row["cells"]}
            anchors = {text for text in anchors if len(text) >= 6 and any(c.isalpha() for c in text)}
            if len(anchors) >= 2 and all(text in body_by_annex[owner] for text in anchors):
                return owner
        return None
    joined = compact(" ".join(cell["text"] for row in grid["rows"] for cell in row["cells"]))
    matches = [key for key, body in body_by_annex.items() if len(joined) >= 24 and joined in body]
    return matches[0] if len(matches) == 1 else None


def grid_record(page_number, grid):
    rows = grid.extract()
    # Missing coordinates usually indicate a merged cell or incomplete grid.
    # Only emit regular grids whose geometry the parser recovered.
    if (len(rows) < 2 or len(rows[0]) < 2 or
            any(len(row) != len(rows[0]) or None in row for row in rows)):
        return None
    return {
        "source": {"page": page_number, "bbox": list(grid.bbox)},
        "rows": [{
            "row": row_number, "row_group": 1, "group_type": "tbody",
            "cells": [{
                "column": column, "text": re.sub(r"\s+", " ", cell).strip(),
                "header": False, "rowspan": 1, "colspan": 1,
                "source": {"page": page_number, "bbox": list(bbox)},
                "links": [], "footnote_ids": [], "image_sources": [],
            } for column, (cell, bbox) in enumerate(zip(row, geometry.cells), 1)],
        } for row_number, (row, geometry) in enumerate(zip(rows, grid.rows), 1)],
    }


def pdf_tables(raw, annexes):
    import pdfplumber
    from pdfminer.psparser import PSException
    from pdfplumber.utils.exceptions import PdfminerException

    body_by_annex = {key: compact(body) for key, (_, body) in annexes.items()}
    tables = defaultdict(list)
    stats = {"candidates": 0, "unlinked_or_non_rectangular": 0, "pages_without_text": 0}
    markers, candidates = [], []
    try:
        with pdfplumber.open(BytesIO(raw)) as pdf:
            for page in pdf.pages:
                try:
                    if not page.chars:
                        stats["pages_without_text"] += 1
                        continue
                    if not annexes:
                        continue
                    for line in page.extract_text_lines(return_chars=False):
                        label = " ".join(line["text"].upper().split())
                        if ANNEX_HEADING.fullmatch(label) or ARTICLE_HEADING.fullmatch(label):
                            markers.append(((page.page_number, line["top"]), label))
                    for grid in page.find_tables():
                        stats["candidates"] += 1
                        candidate = grid_record(page.page_number, grid)
                        if candidate is not None:
                            candidates.append(candidate)
                finally:
                    page.close()
    except (PdfminerException, PSException, ValueError, OSError) as exc:
        stats["read_error"] = type(exc).__name__
        stats["unlinked_or_non_rectangular"] = stats["candidates"]
        return tables, stats

    positions, owners = heading_index(sorted(markers), annexes)
    for grid in candidates:
        if owner := table_owner(grid, body_by_annex, positions, owners):
            tables[owner].append(grid)
    stats["unlinked_or_non_rectangular"] = stats["candidates"] - sum(map(len, tables.values()))
    return tables, stats
