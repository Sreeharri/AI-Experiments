"""Build a versioned index and assemble evidence without a model."""

from collections import Counter, defaultdict
import re

from .document_recovery import recover_document
from .pdf_source import pdf_tables
from .references import Resolver
from .xhtml import AnnexLocatorError, XHTML, payload_type

STOP_WORDS = set("""
a an and article as at annex be been being by can could for from in into is it its
may must no not of on or paragraph regulation shall should that the their them
these this those to under union upon was were where which who will with would
member members state states may also such any only
""".split())
TEXT_ELEMENT_TYPES = {"ARTICLE", "PARAGRAPH", "UNNUMBERED_PARAGRAPH", "POINT",
                      "SUBPOINT", "INDENT", "RECITAL"}


def words(text):
    return {token for token in re.findall(r"[a-z][a-z-]{3,}", text.lower())
            if token not in STOP_WORDS}


def unique(values):
    return list(dict.fromkeys(values))


def article_context(index, source_id):
    current = index.get(source_id)
    while current:
        if current["kind"] in {"ARTICLE", "ANNEX", "RECITAL"}:
            return current
        current = index.get(current["parent_id"])
    return None


def flatten_children(children, node_id):
    for child in children[node_id]:
        yield child
        yield from flatten_children(children, child)


class NoUsableProvisions(ValueError):
    """The selected version has no usable structured provision text."""


def build(data, raw_xhtml, xhtml_origin, *, recover_from_database=False, recover_document_text=False):
    is_pdf = recover_from_database and payload_type(raw_xhtml) == "PDF"
    xhtml = None if is_pdf else XHTML(raw_xhtml)
    additions, recovery = [], None
    if recover_document_text and not any(row["element_type"] in {"ARTICLE", "ANNEX"}
                                         for row in data["legal_elements"]):
        additions, recovery = recover_document(data, raw_xhtml, xhtml)
        if not additions:
            raise NoUsableProvisions("The selected version has no usable provision or full-document text.")
    raw_elements = sorted([*data["legal_elements"], *additions], key=lambda item: item["ordinal"])
    elements = {}
    children = defaultdict(list)
    for row in raw_elements:
        node_id = row["id"]
        parent = row.get("parent_id")
        if node_id in elements or parent is not None and parent not in elements:
            raise ValueError(f"Duplicate element or invalid parent order: {row['identifier']}")
        elements[node_id] = {
            "id": node_id, "kind": row["element_type"], "label": row["identifier"],
            "number": row.get("number"), "path": row["path"], "ordinal": row["ordinal"],
            "parent_id": parent, "heading": row.get("heading"),
            "text": row.get("body_text") or "",
            "source_locator": row.get("source_locator"),
            "aggregate_text": bool((row.get("details") or {}).get("aggregate_body")),
            **({"recovery_source": row["recovery_source"]} if "recovery_source" in row else {}),
        }
        if parent:
            children[parent].append(node_id)

    articles = [n for n in elements.values() if n["kind"] == "ARTICLE"]
    annexes = [n for n in elements.values() if n["kind"] == "ANNEX"]
    recitals = [n for n in elements.values() if n["kind"] == "RECITAL"]
    sections, tables, images = [], [], []
    database_annexes = []
    missing_targets = []

    def database_body(node):
        body = node["text"].strip()
        if body and node["aggregate_text"]:
            return body
        pieces = [body] if body else []
        for key in children[node["id"]]:
            child = database_body(elements[key])
            if child and " ".join(child.split()) not in " ".join(body.split()):
                pieces.append(child)
        return "\n\n".join(pieces)

    if is_pdf and not recovery and not any(database_body(node) for node in articles + annexes):
        raise NoUsableProvisions("The pinned PDF has no usable PostgreSQL article or annex text.")
    annex_bodies = {node["id"]: database_body(node) for node in annexes} if recover_from_database else {}
    grids, pdf_stats = pdf_tables(raw_xhtml, {
        node["id"]: (node["label"], annex_bodies[node["id"]]) for node in annexes
    }) if is_pdf and (not recovery or annexes) else ({}, None)

    def database_annex(row):
        annex_id = row["id"]
        body = annex_bodies[annex_id]
        source = row.get("recovery_source") or {"kind": "POSTGRESQL_LEGAL_ELEMENT", "element_id": annex_id}
        section_id = f"{annex_id}:database:1"
        grid_ids = []
        for index, grid in enumerate(grids.get(annex_id, ()), 1):
            table_id = f"{annex_id}:pdf-table:{index}"
            grid_ids.append(table_id)
            tables.append({
                **grid, "id": table_id, "annex_id": annex_id, "part": None,
                "published_number": None, "caption": [], "footnote_ids": [],
                "section_id": section_id, "section_label": "Stored annex text",
                "origin": "PDF_PAGE_GRID", "_source_text_keys": [],
            })
        sections.append({
            "id": section_id, "kind": "SECTION", "label": "Stored annex text",
            "annex_id": annex_id, "part": None, "headings": [], "source": source,
            "blocks": [{"text": body, "source": source, "footnote_ids": []}]
                      + [{"table_id": key} for key in grid_ids],
            "table_ids": grid_ids, "image_ids": [],
        })

    for row in raw_elements:
        if row["element_type"] != "ANNEX":
            continue
        if xhtml is not None:
            try:
                new_sections, new_tables, new_images = xhtml.annex(
                    row, annex_bodies.get(row["id"])
                )
            except AnnexLocatorError:
                if not recover_from_database:
                    raise
            else:
                sections.extend(new_sections)
                tables.extend(new_tables)
                images.extend(new_images)
                continue
        database_annexes.append(row["id"])
        if annex_bodies[row["id"]]:
            database_annex(row)
        else:
            missing_targets.append({"id": row["id"], "kind": "ANNEX", "reason": "MISSING_ANNEX_TEXT"})

    partial = is_pdf or bool(database_annexes) or bool(recovery)
    if partial:
        for article in articles:
            if not article["text"].strip():
                article["text"] = database_body(article)
            if not article["text"]:
                missing_targets.append({
                    "id": article["id"], "kind": "ARTICLE", "reason": "MISSING_ARTICLE_TEXT",
                })
    missing_ids = {item["id"] for item in missing_targets}

    db_tables = defaultdict(list)
    for element in elements.values():
        if element["kind"] == "TABLE":
            annex = article_context(elements, element["id"])
            if annex and annex["kind"] == "ANNEX":
                db_tables[annex["id"], " ".join(element["text"].split())].append(element["id"])
    for table in tables:
        matches = unique(key for source_text in table.pop("_source_text_keys")
                         for key in db_tables[table["annex_id"], source_text])
        table["database_table_candidate_ids"] = matches
    match_counts = Counter(key for table in tables for key in table["database_table_candidate_ids"])
    for table in tables:
        matches = table["database_table_candidate_ids"]
        table["database_table_id"] = matches[0] if len(matches) == 1 and match_counts[matches[0]] == 1 else None

    section_index = {item["id"]: item for item in sections}
    table_index = {item["id"]: item for item in tables}
    tables_by_annex = defaultdict(list)
    for item in tables:
        tables_by_annex[item["annex_id"]].append(item)
    resolver = Resolver(data["celex"], elements, tables)
    refs = []
    seen = set()

    def add_refs(items):
        for item in items:
            key = (item["source_id"], item["kind"], item["target_locator"],
                   item["target_instrument"], item["source_excerpt"])
            if key in seen:
                continue
            seen.add(key)
            refs.append(item)

    for node in elements.values():
        if node["kind"] not in TEXT_ELEMENT_TYPES or not node["text"]:
            continue
        owner = article_context(elements, node["id"])
        if owner is None or owner["kind"] == "ANNEX":
            continue  # Annex citations are read from scoped XHTML blocks below.
        own_text = node["text"]
        for child_id in children[node["id"]]:
            child_text = elements[child_id]["text"]
            if child_text:
                own_text = own_text.replace(child_text, " ", 1)
        add_refs(resolver.links(node["id"], own_text))

    for section in sections:
        for block in section["blocks"]:
            if block.get("text"):
                add_refs(resolver.links(
                    section["id"], block["text"], section["annex_id"], section["part"]
                ))

    # Aggregate article text can repeat a citation already extracted at a more precise location.
    nested_mentions = set()
    for ref in refs:
        current = elements.get(ref["source_id"])
        while current and current["parent_id"]:
            parent = elements[current["parent_id"]]
            nested_mentions.add((parent["id"], ref["kind"], ref["target_locator"], ref["matched_text"]))
            current = parent
    refs = [ref for ref in refs if not (
        elements.get(ref["source_id"], {}).get("aggregate_text")
        and (ref["source_id"], ref["kind"], ref["target_locator"], ref["matched_text"]) in nested_mentions
    )]
    forwards, reverse = defaultdict(list), defaultdict(list)
    for index, ref in enumerate(refs, 1):
        ref["id"] = f"ref:{index}"
        forwards[ref["source_id"]].append(ref)
        if ref["status"] == "RESOLVED":
            reverse[ref["target_id"]].append(ref)

    recital_terms = {r["id"]: words(r["text"]) for r in recitals}
    frequency = Counter(term for terms in recital_terms.values() for term in terms)

    def supporting_recitals(target_ids, target_text):
        explicit = {}
        for target_id in target_ids:
            for ref in reverse[target_id]:
                source = elements.get(ref["source_id"])
                if source and source["kind"] == "RECITAL":
                    explicit.setdefault(source["id"], []).append(ref["id"])
        values = [
            {"recital_id": key, "kind": "EXPLICIT_CITATION", "reference_ids": ids}
            for key, ids in explicit.items()
        ]
        terms = words(target_text)
        ranked = []
        for key, tokens in recital_terms.items():
            matched = terms & tokens
            if key in explicit or len(matched) < 2:
                continue
            score = sum(1 / frequency[t] for t in matched)
            ranked.append((score, elements[key]["ordinal"], key, sorted(matched)))
        for _, _, key, matched in sorted(ranked, key=lambda item: (-item[0], item[1]))[:3]:
            values.append({
                "recital_id": key, "kind": "SUPPORTING_CONTEXT_CANDIDATE",
                "matched_terms": matched,
            })
        return values

    units = []
    sections_by_annex = defaultdict(list)
    for section in sections:
        sections_by_annex[section["annex_id"]].append(section)

    def add_unit(kind, unit_id, label, text, source_ids, annex_id=None, table_ids=(),
                 note_ids=(), image_ids=()):
        outgoing = [ref for key in source_ids for ref in forwards[key]]
        incoming = [ref for key in source_ids for ref in reverse[key]]
        if annex_id and kind != "ANNEX":
            incoming.extend(reverse[annex_id])
        governing = []
        for ref in incoming:
            owner = article_context(elements, ref["source_id"])
            if owner and owner["kind"] == "ARTICLE":
                governing.append(ref["id"])
        cited_tables = [r["target_id"] for r in outgoing
                        if r["status"] == "RESOLVED" and r["kind"] == "CITES_TABLE"]
        # Keep broad annex references separate from explicitly cited or owned tables.
        candidate_tables = []
        for ref in outgoing:
            target = elements.get(ref["target_id"])
            if target and target["kind"] == "ANNEX":
                candidate_tables.extend(table["id"] for table in tables_by_annex[target["id"]])
            if target and target["kind"] == "ANNEX_PART":
                candidate_tables.extend(
                    table["id"] for table in tables_by_annex[target["parent_id"]]
                    if table["part"] == str(target["number"]).upper()
                )
        table_ids = unique([*table_ids, *cited_tables])
        units.append({
            "id": unit_id, "kind": kind, "label": label, "annex_id": annex_id,
            "source_ids": list(source_ids),
            "table_ids": table_ids,
            "candidate_table_ids": [key for key in unique(candidate_tables) if key not in table_ids],
            "forward_reference_ids": unique(r["id"] for r in outgoing),
            "governing_article_reference_ids": unique(governing),
            "reference_issues": unique(
                r["id"] for r in outgoing if r["status"] != "RESOLVED"
            ),
            "footnote_ids": unique(note_ids), "image_ids": unique(image_ids),
            "recital_context": supporting_recitals(source_ids, text),
        })

    for article in articles:
        if article["id"] in missing_ids:
            continue
        article["footnote_ids"] = xhtml.notes_for(article["source_locator"]) if xhtml else []
        own = [article["id"], *flatten_children(children, article["id"])]
        add_unit("ARTICLE", article["id"], article["label"], article["text"], own,
                 note_ids=article["footnote_ids"])

    for row in additions:
        if row["element_type"] == "DOCUMENT_TEXT":
            add_unit("DOCUMENT_TEXT", row["id"], row["identifier"], row["body_text"], [row["id"]])

    for annex in annexes:
        if annex["id"] in missing_ids:
            continue
        group = sections_by_annex[annex["id"]]
        add_unit("ANNEX", annex["id"], annex["label"], annex["text"], [annex["id"]],
                 annex_id=annex["id"], table_ids=[t for s in group for t in s["table_ids"]],
                 note_ids=xhtml.notes_for(annex["source_locator"]) if xhtml else [])
        for section in group:
            if not section["blocks"]:
                continue
            blocks = section["blocks"]
            add_unit(
                section["kind"], section["id"], section["label"],
                " ".join(b.get("text", "") for b in blocks), [section["id"]],
                annex_id=annex["id"], table_ids=section["table_ids"],
                note_ids=[n for b in blocks for n in b.get("footnote_ids", [])],
                image_ids=section["image_ids"],
            )
    for table in tables:
        parent = elements[table["annex_id"]]["label"]
        part = f", Part {table['part']}" if table["part"] else ""
        title = (f"Table {table['published_number']}" if table["published_number"]
                 else f"unnumbered table {table['id'].split(':')[-1]}")
        row_text = " ".join(cell["text"] for row in table["rows"] for cell in row["cells"])
        section = section_index[table["section_id"]]
        add_unit("TABLE", table["id"], f"{parent}{part}, {title}", row_text, [table["id"]],
                 annex_id=table["annex_id"], table_ids=[table["id"]],
                 note_ids=table["footnote_ids"], image_ids=section["image_ids"])

    counts = Counter(unit["kind"] for unit in units)
    if partial and not any(counts[kind] for kind in ("ARTICLE", "ANNEX", "TABLE", "DOCUMENT_TEXT")):
        raise NoUsableProvisions("Recovery did not find a usable article, annex, or table.")
    pdf_table_count = sum(table.get("origin") == "PDF_PAGE_GRID" for table in tables)
    version = data["version"]
    return {
        "schema_version": 1,
        "document": {
            "celex": data["celex"],
            "title": data.get("title") or (data.get("base_document") or {}).get("title_en"),
            "version_id": version["id"], "language": version["language"],
            "version_kind": version.get("version_kind"), "source_url": version.get("source_url"),
            "source_sha256": version["content_sha256"],
            "xhtml_origin": xhtml_origin if xhtml else None,
            "source_origin": xhtml_origin,
            "source_parser": xhtml.parser_mode if xhtml else "PDF_DATABASE",
            "annex_locator_recoveries": [
                item for item in xhtml.annex_locator_recoveries if item["annex_id"] not in database_annexes
            ] if xhtml else [],
            **({"source_coverage": {
                "status": "PARTIAL", "database_annex_ids": database_annexes,
                "missing_targets": missing_targets, "pdf_table_scan": pdf_stats,
                **({"document_recovery": recovery} if recovery else {}),
                "limitations": [
                    "PDF tables are page-local grids. Merged or unlinked grids, footnotes, and images are unverified."
                    if is_pdf else
                    "Source tables, footnotes, and images are unverified in annexes using stored text."
                ] + (["Targets were recovered from headings in full text. Check provision boundaries and source layout."]
                     if recovery == "HEADING_RECOVERY" else
                     ["Full text is available for document-level review. No article or annex targets were assigned."]
                     if recovery else [])
                  + (["German citation resolution requires review."] if version["language"] == "DE" else []),
            }} if partial else {}),
        },
        "summary": {
            "articles": counts["ARTICLE"], "annexes": counts["ANNEX"],
            "xhtml_data_tables": counts["TABLE"] - pdf_table_count, "pdf_data_tables": pdf_table_count,
            "document_text_targets": counts["DOCUMENT_TEXT"],
            "recitals": len(recitals),
            "database_table_elements": sum(item["kind"] == "TABLE" for item in elements.values()),
            "annex_section_units": counts["SECTION"], "bat_units": counts["BAT"],
            "stored_reference_rows": len(data["element_references"]),
            "resolved_references": sum(r["status"] == "RESOLVED" for r in refs),
            "unresolved_references": sum(r["status"] == "UNRESOLVED" for r in refs),
            "external_text_required": sum(r["status"] == "EXTERNAL_TEXT_REQUIRED" for r in refs),
            "formula_images": sum(i["review_status"] == "VERIFY_FORMULA_AGAINST_PDF" for i in images),
        },
        "document_context": xhtml.document_context() if xhtml else {
            "preamble_before_recitals": [], "closing": [],
        },
        "elements": elements, "sections": section_index, "tables": table_index,
        "footnotes": xhtml.notes if xhtml else {}, "images": {image["id"]: image for image in images},
        "stored_references_for_audit": sorted(data["element_references"], key=lambda r: r["id"]),
        "explicit_references": refs,
        "incoming_reference_index": {key: [r["id"] for r in value] for key, value in reverse.items() if value},
        "units": units,
    }


def evidence_for(data, identifier):
    matches = [unit for unit in data["units"] if identifier in (unit["id"], unit["label"])]
    if len(matches) != 1:
        raise ValueError(f"Expected one unit for {identifier!r}, found {len(matches)}. Use its exact ID.")
    unit = matches[0]
    refs = {r["id"]: r for r in data["explicit_references"]}
    reference_ids = unique(unit["forward_reference_ids"] + unit["governing_article_reference_ids"])
    text_context, missing_notes = {}, []
    table_ids = unit["table_ids"]
    sections = data["sections"]
    elements = data["elements"]
    notes = list(unit["footnote_ids"])
    image_ids = list(unit["image_ids"])
    for table_id in table_ids:
        table = data["tables"][table_id]
        notes.extend(table["footnote_ids"])
        # The same section may contain several tables and a formula.
        section = sections[table["section_id"]]
        notes.extend(n for b in section["blocks"] for n in b.get("footnote_ids", []))
        image_ids.extend(section["image_ids"])
    for key in reference_ids:
        ref = refs[key]
        target = ref["source_id"] if key in unit["governing_article_reference_ids"] else ref["target_id"]
        if target in elements:
            element = elements[target]
            text_context[target] = element
            notes.extend(element.get("footnote_ids", []))
            owner = article_context(elements, target)
            if owner and owner["id"] != target and owner["kind"] == "ARTICLE":
                text_context[owner["id"]] = owner
                notes.extend(owner.get("footnote_ids", []))
    for key in unique(notes):
        if key not in data["footnotes"]:
            missing_notes.append(key)
    target_elements = [elements[key] for key in unit["source_ids"] if key in elements]
    section_ids = [key for key in unit["source_ids"] if key in sections]
    section_ids.extend(data["tables"][key]["section_id"] for key in table_ids)
    return {
        "document": data["document"], "unit": unit,
        "target_elements": target_elements,
        "linked_legal_text": list(text_context.values()),
        "annex_sections": [sections[key] for key in unique(section_ids)],
        "tables": [data["tables"][key] for key in table_ids],
        "candidate_tables_from_broad_references": [
            {k: data["tables"][key][k] for k in ("id", "annex_id", "part", "published_number", "caption")}
            for key in unit["candidate_table_ids"]
        ],
        "footnotes": {key: data["footnotes"][key] for key in unique(notes) if key in data["footnotes"]},
        "missing_footnotes": missing_notes,
        "images": [data["images"][key] for key in unique(image_ids)],
        "recitals": [
            {**candidate, "text": elements[candidate["recital_id"]]["text"]}
            for candidate in unit["recital_context"]
        ],
        "explicit_references": [refs[key] for key in reference_ids],
    }
