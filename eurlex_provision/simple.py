"""Prepare one complete provision and its direct references for a single decision."""

import json

from .evidence import norm
from .screening import LEVELS, ModelError


DECISION_FIELDS = ("level", "reason", "area")
EVIDENCE_FIELDS = ("target_quote", "anchor_quotes")
ANCHOR_SOURCE_FIELDS = ("company_facts", "scope", "roles", "note")


def build_pack(evidence, unit, anchors, supporting_ids=()):
    key = unit["id"]
    kind = unit["kind"]
    included, note_ids = set(), list(unit["footnote_ids"])

    def table_record(table_id):
        table = evidence.tables[table_id]
        included.add(table_id)
        note_ids.extend(table["footnote_ids"])
        rows = []
        for row in table["rows"]:
            cells = []
            for cell in row["cells"]:
                note_ids.extend(cell["footnote_ids"])
                item = {"column": cell["column"], "text": cell["text"]}
                for field in ("rowspan", "colspan"):
                    if cell[field] > 1:
                        item[field] = cell[field]
                if cell["header"]:
                    item["header"] = True
                cells.append(item)
            rows.append(cells)
        return {"locator": evidence.locator(table_id), "caption": table["caption"], "rows": rows}

    def section_record(section_id):
        section = evidence.sections[section_id]
        included.add(section_id)
        blocks = []
        for block in section["blocks"]:
            note_ids.extend(block.get("footnote_ids", []))
            if table_id := block.get("table_id"):
                blocks.append({"table": table_record(table_id)})
            elif text := block.get("text"):
                blocks.append({"text": text})
        item = {"locator": evidence.locator(section_id), "blocks": blocks}
        if section["headings"]:
            item["headings"] = section["headings"]
        if section["image_ids"]:
            item["unavailable_images"] = [
                {"locator": image_id, "alt": evidence.data["images"][image_id].get("alt")}
                for image_id in section["image_ids"]
            ]
        return item

    def record(source_id):
        included.add(source_id)
        if source_id in evidence.tables:
            return table_record(source_id)
        if source_id in evidence.sections:
            return section_record(source_id)
        element = evidence.element(source_id)
        note_ids.extend(element["note_ids"])
        source = evidence.elements[source_id]
        if source["kind"] in {"ANNEX", "ANNEX_PART"}:
            sections = evidence.section_order[source_id if source["kind"] == "ANNEX" else source["parent_id"]]
            if source["kind"] == "ANNEX_PART":
                sections = [s for s in sections if norm(evidence.sections[s]["part"]) == norm(source["number"])]
            if sections:
                return {"locator": element["locator"], "sections": [
                    section_record(section_id) for section_id in sections
                    if evidence.sections[section_id]["blocks"]
                ]}
        item = {"locator": element["locator"], "text": element["text"]}
        if element["heading"]:
            item["heading"] = element["heading"]
        return item

    provision = record(key)
    source_ids = list(dict.fromkeys([*unit["source_ids"], *sorted(included)]))
    context = []
    if kind == "TABLE":
        parent = evidence.tables[key]["section_id"]
        section = evidence.section(parent)
        note_ids.extend(section["note_ids"])
        included.add(parent)
        if section["text"]:
            context.append({"locator": section["locator"], "text": section["text"]})
    if unit["annex_id"] is not None:
        for section_id in evidence.section_order[unit["annex_id"]]:
            if (section_id not in included and norm(evidence.sections[section_id]["label"]) == "scope"
                    and evidence.sections[section_id]["blocks"]):
                context.append(section_record(section_id))

    refs = {ref["id"]: ref for source_id in source_ids for ref in evidence.forward[source_id]}
    for ref_id in unit["forward_reference_ids"]:
        refs[ref_id] = evidence.refs[ref_id]
    # An entire annex contains its parts and tables, so include it first when cited.
    refs = sorted(refs.values(), key=lambda ref: {
        "ANNEX": 0, "ANNEX_PART": 1,
    }.get(evidence.elements.get(ref["target_id"], {}).get("kind"), 2))
    direct_refs, unavailable = [], {}
    for ref in refs:
        if ref["status"] == "RESOLVED":
            target_id = ref["target_id"]
            # Use the full article even when the citation is to one of its paragraphs.
            while target_id in evidence.elements:
                element = evidence.elements[target_id]
                if element["kind"] in {"ARTICLE", "ANNEX", "ANNEX_PART"}:
                    break
                if element["parent_id"] not in evidence.elements:
                    break
                target_id = element["parent_id"]
            source = evidence.elements.get(target_id, {})
            part_is_covered = source.get("kind") == "ANNEX_PART" and source["parent_id"] in included
            if target_id not in included and not part_is_covered:
                direct_refs.append(record(target_id))
        else:
            item, reason = evidence.retrieve(ref["id"], ref["target_instrument"])
            identity = (ref["target_instrument"], ref["target_locator"])
            if identity not in unavailable:
                if item is not None:
                    direct_refs.append(item)
                unavailable[identity] = reason
    context.extend(record(source_id) for source_id in supporting_ids if source_id not in included)
    return {
        "document": {field: evidence.document[field] for field in ("celex", "version_id", "title")},
        "provision": provision,
        "direct_references": direct_refs,
        "essential_context": context,
        "footnotes": evidence.note_evidence(note_ids),
        "unavailable_references": [
            {"instrument": instrument, "locator": locator, "reason": reason}
            for (instrument, locator), reason in unavailable.items() if reason is not None
        ],
        "business_anchors": anchors,
    }


def target_text(value):
    """Yield source text from the target body, including table cells."""
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            yield value["text"]
        for child in value.values():
            yield from target_text(child)
    elif isinstance(value, list):
        for child in value:
            yield from target_text(child)


def contains_quote(quote, sources):
    if not isinstance(quote, str):
        return False
    normalized = " ".join(quote.split())
    return 0 < len(normalized) <= 240 and any(
        normalized in " ".join(source.split()) for source in sources if isinstance(source, str)
    )


def validate_decision(decision, pack):
    if not isinstance(decision, dict) or set(decision) != set(DECISION_FIELDS + EVIDENCE_FIELDS):
        raise ModelError("Expected level, reason, area, target_quote and anchor_quotes.")
    level, reason, area = (decision[field] for field in DECISION_FIELDS)
    if level not in LEVELS:
        raise ModelError("Expected level DIRECT, INDIRECT or IGNORE.")
    if not isinstance(reason, str) or not 0 < len(reason.strip()) <= 600:
        raise ModelError("Expected a non-empty reason of at most 600 characters.")
    if (not isinstance(area, list) or len(area) > 4
            or any(not isinstance(item, str) or item not in pack["business_anchors"] for item in area)
            or len(area) != len(set(area))):
        raise ModelError("Expected up to four distinct area IDs from the supplied business anchors.")
    if bool(area) != (level != "IGNORE"):
        raise ModelError("Expected matching area IDs for DIRECT or INDIRECT, and [] for IGNORE.")
    if not contains_quote(decision["target_quote"], target_text(pack["provision"])):
        raise ModelError("Copy target_quote from the target provision itself, including a table cell if relevant.")
    quotes = decision["anchor_quotes"]
    if (not isinstance(quotes, list) or len(quotes) != len(area)
            or any(not isinstance(item, dict) or set(item) != {"id", "quote"} for item in quotes)
            or [item["id"] for item in quotes] != area):
        raise ModelError("Provide one anchor quote per area ID, in the same order as area.")
    for item in quotes:
        anchor = pack["business_anchors"][item["id"]]
        sources = []
        for field in ANCHOR_SOURCE_FIELDS:
            value = anchor.get(field)
            sources.extend(value if isinstance(value, list) else [value])
        if not contains_quote(item["quote"], sources):
            raise ModelError(f"Copy the quote for {item['id']} from its company_facts, scope, roles or note.")
    return {"level": level, "reason": reason.strip(), "area": area}



def score(client, prompt, pack):
    anchor_ids = list(pack["business_anchors"])
    quote_schema = {"type": "string", "minLength": 1, "maxLength": 240}
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": list(EVIDENCE_FIELDS + DECISION_FIELDS),
        "properties": {
            "target_quote": quote_schema,
            "anchor_quotes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "quote"],
                    "properties": {
                        "id": {"type": "string", "enum": anchor_ids},
                        "quote": quote_schema,
                    },
                },
                "maxItems": 4,
            },
            "level": {"type": "string", "enum": list(LEVELS)},
            "reason": {"type": "string", "minLength": 1, "maxLength": 600},
            "area": {
                "type": "array",
                "items": {"type": "string", "enum": anchor_ids},
                "maxItems": 4,
            },
        },
    }
    messages = [{"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(pack, ensure_ascii=False)}]
    rejected_reply = None
    for attempt in range(2):
        reply = client.chat(messages, thinking=False, stream=False, seed=42, sampling={"temperature": 0},
                            schema=schema, schema_name="provision_relevance_evidence", max_tokens=1024)
        if not reply.ok:
            if reply.error_kind == "context_limit":
                raise ModelError("The complete evidence pack exceeds the model context limit.")
            raise ModelError(client.clean(reply.error or "Model request failed."))
        candidate, error = reply.parse_json()
        try:
            if error:
                raise ModelError(error)
            decision = validate_decision(candidate, pack)
        except ModelError as exc:
            if attempt:
                raise ModelError(f"Invalid response after one correction request: {exc}") from exc
            rejected_reply = {"raw_reply": reply.content, "error": str(exc), "usage": reply.usage}
            messages[0]["content"] = f"{prompt}\nCorrection required for the previous response: {exc}"
        else:
            audit = {"finish_reason": reply.finish_reason, "usage": reply.usage,
                     "raw_reply": reply.content, "evidence": {key: candidate[key] for key in EVIDENCE_FIELDS}}
            if rejected_reply is not None:
                audit["rejected_reply"] = rejected_reply
            return decision, audit
