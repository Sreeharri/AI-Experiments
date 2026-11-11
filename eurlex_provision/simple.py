"""Prepare one complete provision and its direct references for a single decision."""

import json
import re

from .evidence import norm
from .screening import LEVELS, ModelError


DECISION_FIELDS = ("level", "reason", "area")
REPLY_FIELDS = ("target_passage", "anchor_sources", "level", "reason")
ANCHOR_SOURCE_FIELDS = ("company_facts", "scope", "roles", "note")


class TargetScreeningError(ModelError):
    """A failed request or reply specific to a single provision."""

    def __init__(self, message, attempts=()):
        super().__init__(message)
        self.attempts = list(attempts)


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


def target_passages(provision):
    excerpts = dict.fromkeys(
        match.group()
        for text in target_text(provision)
        for match in re.finditer(r"\S+(?:\s+\S+){0,34}", text)
    )
    return {f"P{index}": excerpt for index, excerpt in enumerate(excerpts, 1)}


def anchor_sources(anchors):
    sources = {}
    for anchor_id, anchor in anchors.items():
        for field in ANCHOR_SOURCE_FIELDS:
            value = anchor.get(field)
            if isinstance(value, str) and value.strip():
                sources[f"{anchor_id}:{field}"] = (anchor_id, value)
            elif isinstance(value, list):
                for index, text in enumerate(value, 1):
                    if isinstance(text, str) and text.strip():
                        sources[f"{anchor_id}:{field}.{index}"] = (anchor_id, text)
    return sources


def validate_decision(decision, passages, sources):
    if not isinstance(decision, dict) or set(decision) != set(REPLY_FIELDS):
        raise ModelError("Expected level, reason, target_passage and anchor_sources.")
    level, reason = decision["level"], decision["reason"]
    if level not in LEVELS:
        raise ModelError("Expected level DIRECT, INDIRECT or IGNORE.")
    if not isinstance(reason, str) or not 0 < len(reason.strip()) <= 600:
        raise ModelError("Expected a non-empty reason of at most 600 characters.")
    passage_id = decision["target_passage"]
    if not isinstance(passage_id, str) or passage_id not in passages:
        raise ModelError("Select a target_passage ID from the supplied passages.")
    source_ids = decision["anchor_sources"]
    if (not isinstance(source_ids, list) or len(source_ids) > 4
            or any(not isinstance(key, str) or key not in sources for key in source_ids)):
        raise ModelError("Select up to four IDs from anchor_source_options.")
    if level == "IGNORE":
        source_ids = []
    elif not source_ids:
        raise ModelError("Select at least one anchor source for DIRECT or INDIRECT.")
    source_ids = list(dict.fromkeys(source_ids))
    area = list(dict.fromkeys(sources[key][0] for key in source_ids))
    evidence = {
        "target_passage": passage_id,
        "target_quote": passages[passage_id],
        "anchor_quotes": [
            {"id": sources[key][0], "source": key, "quote": sources[key][1]} for key in source_ids
        ],
    }
    return {"level": level, "reason": reason.strip(), "area": area}, evidence



def score(client, prompt, pack):
    passages = target_passages(pack["provision"])
    if not passages:
        raise TargetScreeningError("The target has no text available for a passage citation.")
    sources = anchor_sources(pack["business_anchors"])
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": list(REPLY_FIELDS),
        "properties": {
            "target_passage": {"type": "string", "enum": list(passages)},
            "anchor_sources": {
                "type": "array",
                "items": {"type": "string", "enum": list(sources)},
                "maxItems": 4,
            },
            "level": {"type": "string", "enum": list(LEVELS)},
            "reason": {"type": "string", "minLength": 1, "maxLength": 600},
        },
    }
    source_options = {}
    for source_id, (anchor_id, _) in sources.items():
        source_options.setdefault(anchor_id, []).append(source_id)
    request_pack = {**pack, "target_passages": passages, "anchor_source_options": source_options}
    messages = [{"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(request_pack, ensure_ascii=False)}]
    rejected_reply = None
    for attempt in range(2):
        reply = client.chat(messages, thinking=False, stream=False, seed=42, sampling={"temperature": 0},
                            schema=schema, schema_name="provision_relevance_sources", max_tokens=1024)
        if not reply.ok:
            if reply.error_kind == "context_limit":
                raise TargetScreeningError("The complete evidence pack exceeds the model context limit.")
            raise ModelError(client.clean(reply.error or "Model request failed."))
        candidate, error = reply.parse_json()
        try:
            if error:
                raise ModelError(error)
            decision, evidence = validate_decision(candidate, passages, sources)
        except ModelError as exc:
            rejected = {"raw_reply": reply.content, "error": str(exc), "usage": reply.usage,
                        "finish_reason": reply.finish_reason}
            if attempt:
                raise TargetScreeningError(
                    f"Invalid response after one correction request: {exc}", [rejected_reply, rejected]
                ) from exc
            rejected_reply = rejected
            messages[0]["content"] = f"{prompt}\nCorrection required for the previous response: {exc}"
        else:
            audit = {"finish_reason": reply.finish_reason, "usage": reply.usage,
                     "raw_reply": reply.content, "evidence": evidence}
            if rejected_reply is not None:
                audit["rejected_reply"] = rejected_reply
            return decision, audit
