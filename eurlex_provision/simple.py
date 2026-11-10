"""Prepare one complete provision and its direct references for a single decision."""

import json

from .evidence import norm
from .screening import LEVELS, ModelError


def build_pack(evidence, unit, document_result, scenario, anchors):
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
    return {
        "document": {field: evidence.document[field] for field in ("celex", "version_id", "title")},
        "document_level_result": document_result,
        "business_facts": scenario["facts"] if scenario else [],
        "business_facts_status": scenario["evidence_status"] if scenario else None,
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


def score(client, prompt, pack):
    allowed_areas = list(pack["business_anchors"])
    schema = {
        "type": "object", "additionalProperties": False, "required": ["level", "reason", "area"],
        "properties": {
            "level": {"enum": [*LEVELS, "NEEDS_REVIEW"]},
            "reason": {"type": "string", "minLength": 1, "maxLength": 600},
            "area": {"type": "array", "items": {"enum": allowed_areas}, "maxItems": 4},
        },
    }
    messages = [{"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(pack, ensure_ascii=False)}]
    reply = client.chat(messages, thinking=False, stream=False, seed=42, sampling={"temperature": 0},
                        schema=schema, schema_name="provision_relevance_simple", max_tokens=768)
    audit = {"finish_reason": reply.finish_reason, "usage": reply.usage, "raw_reply": reply.content}
    if not reply.ok:
        audit["error_kind"] = reply.error_kind
        if reply.error_kind != "context_limit":
            raise ModelError(client.clean(reply.error or "Model request failed."))
        return {"level": "NEEDS_REVIEW", "reason": "The complete evidence pack exceeds the model context limit.",
                "area": []}, audit

    decision, error = reply.parse_json()
    if (error or not isinstance(decision, dict) or set(decision) != {"level", "reason", "area"}
            or decision["level"] not in (*LEVELS, "NEEDS_REVIEW")
            or not isinstance(decision["reason"], str) or not 0 < len(decision["reason"].strip()) <= 600
            or not isinstance(decision["area"], list) or len(decision["area"]) > 4
            or any(not isinstance(area, str) or area not in allowed_areas for area in decision["area"])
            or len(decision["area"]) != len(set(decision["area"]))
            or bool(decision["area"]) != (decision["level"] in ("DIRECT", "INDIRECT"))):
        return {"level": "NEEDS_REVIEW", "reason": error or "The model returned an invalid decision.",
                "area": []}, audit
    return decision, audit
