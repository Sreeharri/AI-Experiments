"""Prepare target branches and retrieve only passages from the pinned evidence."""

from collections import Counter, defaultdict
import json
import re

TARGET_KINDS = {"ARTICLE", "ANNEX", "TABLE"}
BRANCH_LIMIT = 8
BRANCH_CHAR_BUDGET = 6500
CONTEXT_CHAR_BUDGET = 14000


def size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def norm(text):
    return " ".join(str(text).split()).casefold()


def instrument_key(value):
    return norm(value).rstrip(".")


def element_locator(element):
    label = element.get("label") or element["path"]
    if element["kind"] == "RECITAL":
        return f"Recital {element['number']}"
    if label.startswith("Article "):
        label = re.sub(r" (?<!unnumbered )(?:paragraph|point|subpoint) (\w+)", r"(\1)", label)
        label = label.replace(" unnumbered paragraph ", ", unnumbered paragraph ")
    return label


def branch_batches(branches):
    batch, chars = [], 0
    for branch in branches:
        length = size(branch)
        if batch and (len(batch) >= BRANCH_LIMIT or chars + length > BRANCH_CHAR_BUDGET):
            yield batch
            batch, chars = [], 0
        batch.append(branch)
        chars += length
    if batch:
        yield batch


class Evidence:
    def __init__(self, data, supplements):
        self.data = data
        self.document = data["document"]
        self.elements = data["elements"]
        self.sections = data["sections"]
        self.tables = data["tables"]
        self.units = {unit["id"]: unit for unit in data["units"]}
        self.refs = {ref["id"]: ref for ref in data["explicit_references"]}
        self.notes = data["footnotes"]
        self.supplements = supplements
        self.aliases = defaultdict(set)
        self.forward = defaultdict(list)
        self.section_order = defaultdict(list)
        self.section_labels = {}
        self.definitions = []
        self.scopes = []
        labels = Counter(element_locator(element) for element in self.elements.values())
        self.element_labels = {
            key: (f"{element_locator(el)} [source path {el['path']}]"
                  if labels[element_locator(el)] > 1 else element_locator(el))
            for key, el in self.elements.items()
        }
        for ref in self.refs.values():
            self.forward[ref["source_id"]].append(ref)
        for section in self.sections.values():
            group = self.section_order[section["annex_id"]]
            group.append(section["id"])
            annex = self.elements[section["annex_id"]]["label"]
            part = f", Part {section['part']}" if section["part"] else ""
            self.section_labels[section["id"]] = (
                f"{annex}{part}, {section['label']} [section {len(group)}]"
            )
        for key, element in self.elements.items():
            for label in (self.element_labels[key], element_locator(element), element.get("label"), element.get("path")):
                if label:
                    self.aliases[norm(label)].add(key)
            if element["kind"] == "ARTICLE":
                heading = norm(element.get("heading") or "").split(" / ")[-1]
                if heading == "scope":
                    self.scopes.append(key)
        for key, unit in self.units.items():
            self.aliases[norm(unit["label"])].add(key)
            self.aliases[norm(self.locator(key))].add(key)
            if unit["kind"] == "ARTICLE" and "definition" in norm(
                self.elements[key].get("heading") or ""
            ):
                for source in unit["source_ids"][1:]:
                    text = self.elements[source].get("text") or ""
                    match = re.match(r"^\(\d+\)\s*[‘“\"]([^’”\"]+)[’”\"]", text)
                    if match and len(match[1]) > 3:
                        self.definitions.append((match[1].casefold(), source))
        for key, table in self.tables.items():
            for row in table["rows"]:
                locator = self.row_locator(key, row["row"])
                self.aliases[norm(locator)].add((key, row["row"]))

    def locator(self, key):
        if key in self.tables:
            table = self.tables[key]
            unit = self.units[key]
            if table["published_number"]:
                return unit["label"]
            return f"{self.section_labels[table['section_id']]}, {unit['label'].split(', ')[-1]}"
        if key in self.sections:
            return self.section_labels[key]
        return self.element_labels[key]

    def row_locator(self, key, row):
        return f"{self.locator(key)}, row {row}"

    def find_unit(self, label):
        if label in self.units:
            return self.units[label]
        matches = [unit for unit in self.units.values()
                   if norm(label) in (norm(unit["label"]), norm(self.locator(unit["id"])))]
        if len(matches) != 1:
            raise ValueError(f"{self.document['celex']}: expected one unit for {label!r}; use the unit ID")
        return matches[0]

    def note_evidence(self, ids):
        return [
            {"locator": f"Footnote {key}", "source_id": key, **self.notes[key]}
            for key in dict.fromkeys(ids) if key in self.notes
        ]

    def element(self, key):
        el = self.elements[key]
        return {
            "locator": self.locator(key), "source_id": key, "source": el["source_locator"],
            "heading": el.get("heading"), "text": el.get("text") or "",
            "note_ids": el.get("footnote_ids", []),
        }

    def section(self, key):
        section = self.sections[key]
        return {
            "locator": self.locator(key), "source_id": key, "source": section["source"],
            "heading": section["label"], "preceding_headings": section["headings"],
            "text": "\n".join(block["text"] for block in section["blocks"] if block.get("text")),
            "note_ids": [note for b in section["blocks"] for note in b.get("footnote_ids", [])],
        }

    def row(self, key, row):
        return {
            "locator": self.row_locator(key, row["row"]),
            "source_id": key, "row": row["row"], "row_group": row["row_group"],
            "group_type": row["group_type"],
            "cells": [
                {field: cell[field] for field in
                 ("column", "text", "header", "rowspan", "colspan", "source", "footnote_ids")}
                for cell in row["cells"]
            ],
            "note_ids": [note for c in row["cells"] for note in c["footnote_ids"]],
        }

    def table_context(self, key, row_numbers):
        table = self.tables[key]
        selected = set(row_numbers)
        headers = {row["row"] for row in table["rows"]
                   if row["group_type"] == "thead" or
                   (row["cells"] and all(c["header"] for c in row["cells"]))}
        carried = {
            row["row"] for row in table["rows"]
            if any(row["row"] < number < row["row"] + cell["rowspan"]
                   for number in selected for cell in row["cells"])
        }
        return {
            "locator": self.locator(key), "source_id": key, "source": table["source"],
            "caption": table["caption"], "section": self.section_labels[table["section_id"]],
            "note_ids": table["footnote_ids"],
            "header_and_spanning_rows": [
                self.row(key, row) for row in table["rows"]
                if row["row"] in (headers | carried) - selected
            ],
        }

    def table_evidence(self, key):
        return {**self.table_context(key, [row["row"] for row in self.tables[key]["rows"]]),
                "rows": [self.row(key, row) for row in self.tables[key]["rows"]]}

    def article_branches(self, unit, focus):
        article = unit["id"]
        sources = set(unit["source_ids"])
        if focus and focus not in sources:
            raise ValueError("The focus locator is outside the target article.")
        for key in unit["source_ids"][1:]:
            ancestors = []
            parent = self.elements[key]["parent_id"]
            while parent in sources and parent != article:
                ancestors.append(parent)
                parent = self.elements[parent]["parent_id"]
            if focus and focus not in (key, *ancestors):
                continue
            record = self.element(key)
            if not record["text"]:
                continue
            # Ignore the paragraph number when looking for successive dated clauses.
            text = record["text"]
            prefix = re.match(r"^\d+\.\s+", text)
            start = prefix.end() if prefix else 0
            clauses = re.split(r"(?<=\.)\s+(?=From \d{1,2} [A-Z][a-z]+ \d{4},)", text[start:])
            clauses[0] = text[:start] + clauses[0]
            for index, clause in enumerate(clauses, 1):
                locator = record["locator"]
                if len(clauses) > 1:
                    locator += f", sentence {index}"
                yield {**record, "locator": locator, "text": clause,
                       "source_ids": [key, *ancestors, article],
                       "chapeau_locators": [self.locator(k) for k in reversed(ancestors)]}

    def branches(self, unit, focus=None):
        key = unit["id"]
        if unit["kind"] == "ARTICLE":
            branches = list(self.article_branches(unit, focus))
            return branches or [{**self.element(focus or key), "source_ids": [focus or key, key]}]
        if unit["kind"] == "TABLE":
            return [{**self.row(key, row), "source_ids": [key]} for row in self.tables[key]["rows"]]
        section_ids = self.section_order[key] if unit["kind"] == "ANNEX" else [key]
        branches, used_tables = [], set()
        for section_id in section_ids:
            section = self.sections[section_id]
            if any(b.get("text") for b in section["blocks"]):
                branches.append({**self.section(section_id), "source_ids": [section_id]})
            for table in section["table_ids"]:
                if table not in used_tables:
                    branches.extend(self.branches(self.units[table]))
                    used_tables.add(table)
        if branches:
            return branches
        return [{**self.element(key), "source_ids": [key]}] if key in self.elements else []

    def applicable_refs(self, unit, batch):
        sources = dict.fromkeys([key for b in batch for key in b["source_ids"]] + [unit["id"]])
        refs = {ref["id"]: ref for key in sources for ref in self.forward[key]}
        if unit["kind"] != "ARTICLE":
            annex_id = unit["annex_id"]
            table_ids = {b["source_id"] for b in batch if b["source_id"] in self.tables}
            owner_sections = {self.tables[t]["section_id"] for t in table_ids}
            owner_sections.update(k for k in sources if k in self.sections)
            parts = {str(self.sections[s]["part"]).upper() for s in owner_sections
                     if self.sections[s]["part"] is not None}
            for target_id, incoming_ids in self.data["incoming_reference_index"].items():
                el = self.elements.get(target_id, {})
                if target_id == annex_id or target_id in table_ids or (
                    el.get("kind") == "ANNEX_PART" and el.get("parent_id") == annex_id
                    and str(el.get("number")).upper() in parts
                ):
                    for ref_id in incoming_ids:
                        ref = self.refs[ref_id]
                        source = self.elements.get(ref["source_id"])
                        if source and re.search(r"(?:^|/)article/", source["path"]):
                            refs[ref_id] = ref
        return list(refs.values())

    def lookup(self, key):
        if isinstance(key, tuple):
            table, number = key
            row = next(row for row in self.tables[table]["rows"] if row["row"] == number)
            record = self.row(table, row)
            structure = self.table_context(table, [number])
            return {
                **record, "caption": structure["caption"], "section": structure["section"],
                "header_and_spanning_rows": structure["header_and_spanning_rows"],
                "note_ids": record["note_ids"] + structure["note_ids"],
            }
        if key in self.tables:
            return self.table_evidence(key)
        if key in self.sections:
            return {**self.section(key),
                    "available_tables": [self.locator(t) for t in self.sections[key]["table_ids"]]}
        if key in self.elements:
            element = self.elements[key]
            if element["kind"] in {"ANNEX", "ANNEX_PART"}:
                # The locator names a container. Supply its section index and allow an exact follow-up.
                annex = key if element["kind"] == "ANNEX" else element["parent_id"]
                sections = self.section_order[annex]
                if element["kind"] == "ANNEX_PART":
                    sections = [s for s in sections if str(self.sections[s]["part"]).upper()
                                == str(element["number"]).upper()]
                return {**self.element(key), "text": "",
                        "available_sections": [self.locator(s) for s in sections if self.sections[s]["blocks"]]}
            return self.element(key)
        return None

    def retrieve(self, locator, instrument):
        ref = self.refs.get(locator)
        if ref is not None:
            named_instrument = ref.get("target_instrument")
            if instrument and named_instrument and instrument_key(instrument) != instrument_key(named_instrument):
                return None, "Requested instrument conflicts with the reference."
            if ref["status"] == "RESOLVED":
                result = self.lookup(ref["target_id"])
                if result is not None:
                    return result, None
            locator, instrument = ref["target_locator"], named_instrument or instrument
            if not instrument or instrument_key(instrument) == instrument_key(self.document["celex"]):
                return None, "The enriched reference is unresolved or ambiguous."
        if instrument and instrument_key(instrument) != instrument_key(self.document["celex"]):
            item = self.supplements.get((instrument_key(instrument), norm(locator)))
            return (item, None) if item else (None, "External excerpt was not supplied.")
        image = self.data["images"].get(locator)
        if image is not None:
            item = self.supplements.get((instrument_key(self.document["celex"]), norm(locator)))
            if item and item["source_version_id"] == self.document["version_id"]:
                return item, None
            return None, "Provide a verified formula transcription for this exact source version."
        matches = self.aliases.get(norm(locator), set())
        if len(matches) == 1:
            return self.lookup(next(iter(matches))), None
        return None, "Locator was unavailable or ambiguous in the pinned act."

    def initial(self, unit, batch):
        key = unit["id"]
        context, suggestions = {}, []
        remaining = CONTEXT_CHAR_BUDGET

        def add(record, relation, essential=False):
            nonlocal remaining
            if record is None or record["locator"] in context:
                return
            length = size(record)
            if length <= remaining or essential:
                context[record["locator"]] = {**record, "relation": relation}
                remaining -= length
            else:
                suggestions.append(record["locator"])

        refs = self.applicable_refs(unit, batch)
        source_ids = {source for branch in batch for source in branch["source_ids"]}
        if unit["kind"] == "ARTICLE":
            root = self.element(key)
            context[root["locator"]] = {**root, "relation": "FULL_TARGET_ARTICLE"}
            remaining -= size(root)
            for branch in batch:
                for source in branch["source_ids"][1:]:
                    if source != key:
                        add(self.element(source), "TARGET_CHAPEAU")
        by_table = defaultdict(list)
        for branch in batch:
            if "row" in branch:
                by_table[branch["source_id"]].append(branch["row"])
        for table, rows in by_table.items():
            # These are needed to interpret the rows, so keep them even for a large table.
            header = self.table_context(table, rows)
            context[header["locator"]] = {**header, "relation": "TABLE_STRUCTURE"}
            remaining -= size(header)
        if unit["kind"] != "ARTICLE":
            annex_id = unit["annex_id"]
            opening = [s for s in self.section_order[annex_id]
                       if norm(self.sections[s]["label"]) in {"scope", "annex opening and scope"}]
            for section in opening:
                if section not in source_ids and self.section(section)["text"]:
                    add(self.section(section), "ANNEX_SCOPE")
            for section in dict.fromkeys(self.tables[t]["section_id"] for t in by_table):
                if section not in source_ids:
                    add(self.section(section), "TABLE_SECTION")
        for scope in self.scopes:
            if scope != key:
                add(self.element(scope), "ACT_SCOPE", essential=True)
        for ref in sorted(refs, key=lambda r: r["target_id"] not in self.tables):
            if ref["status"] != "RESOLVED":
                continue
            if ref["source_id"] in source_ids:
                if ref["target_id"] not in source_ids:
                    record = self.lookup(ref["target_id"])
                    exact_table = self.tables.get(ref["target_id"])
                    exact_small_table = exact_table is not None and size(record) <= 10000
                    add(record, "EXPLICIT_REFERENCE", essential=exact_small_table)
                    if exact_table is not None:
                        add(self.section(exact_table["section_id"]), "CITED_TABLE_SECTION")
                    linked = self.elements.get(ref["target_id"], {})
                    if linked.get("kind") == "ANNEX":
                        for section in self.section_order[linked["id"]]:
                            if norm(self.sections[section]["label"]) == "scope":
                                add(self.section(section), "LINKED_ANNEX_SCOPE", essential=True)
            elif ref["source_id"] in self.elements:
                add(self.element(ref["source_id"]), "GOVERNING_ARTICLE")
        text = norm(" ".join(b.get("text", "") for b in batch))
        for term, source in self.definitions:
            pattern = re.escape(term) + r"(?:s|es)?"
            if term == "placing on the market":
                pattern = r"(?:placing|placed) on the market"
            if source not in source_ids and re.search(r"(?<!\w)" + pattern + r"(?!\w)", text):
                add(self.element(source), "TERM_DEFINITION")
        for candidate in unit["recital_context"][:3]:
            add(self.element(candidate["recital_id"]), candidate["kind"])
        for ref_id in unit["governing_article_reference_ids"]:
            source = self.refs[ref_id]["source_id"]
            if source in self.elements:
                suggestions.append(self.locator(source))

        image_ids = set(unit["image_ids"])
        for source in source_ids:
            section_id = self.tables[source]["section_id"] if source in self.tables else source
            if section_id in self.sections:
                image_ids.update(self.sections[section_id]["image_ids"])
        images = []
        for image_id in sorted(image_ids):
            image = self.data["images"][image_id]
            if image["review_status"] == "VERIFY_FORMULA_AGAINST_PDF":
                images.append({"locator": image_id, "section": self.locator(image["section_id"]),
                               "status": image["review_status"], "alt": image["alt"],
                               "source": image["source"]})
        return {
            "passages": list(context.values()),
            "reference_links": [
                {field: ref[field] for field in (
                    "id", "source_id", "target_locator", "target_instrument", "status", "issue")}
                for ref in refs
            ],
            "other_context_locators": list(dict.fromkeys(suggestions))[:24],
            "candidate_tables_from_broad_references": [
                self.locator(t) for t in unit["candidate_table_ids"]
            ],
            "formula_images_requiring_pdf_check": images,
        }
