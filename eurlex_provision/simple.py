"""Prepare one provision with limited context and classify it for Max Bögl.

The request is laid out for vLLM prefix caching: the system message is the
same for every request of a run, the user message holds the target evidence,
and the task line comes last. A confirmation request therefore repeats the
first request up to its task line.

The model does not choose a level. It names the actor and the subject of the
target, the catalog entry that describes the subject, and the company role or
indirect path that connects them to Max Bögl. derive() works out the level
from those answers, so a level cannot contradict the answers it rests on.
"""

import json
import re

from .evidence import norm
from .profile import NO_MATCH, NONE, NULL_ALIASES, choices
from .screening import ModelError


DECISION_FIELDS = ("level", "reason", "area")
# UNSURE: the target would apply to a Max Bögl role, but only if a company fact
# that the profile does not hold is true. It is kept for the department.
LEVELS = ("DIRECT", "INDIRECT", "UNSURE", "IGNORE")
KEPT_LEVELS = LEVELS[:3]
REPLY_FIELDS = ("actor", "actor_kind", "subject", "target_passage", "formal_function", "topic",
                "other_anchors", "company_role", "indirect_path", "open_question", "reason")
# Who the actor is. A company role only counts when the actor is on the business side.
BUSINESS, NOT_STATED = "BUSINESS", "NOT_STATED"
ACTOR_KINDS = (BUSINESS, "STATE_OR_AUTHORITY", "EU_OR_INTERNATIONAL_BODY", "PRIVATE_PERSON", NOT_STATED)
# A target is formal only when it has one of these named functions.
FORMAL_FUNCTIONS = (NONE, "ADDRESSEES", "ENTRY_INTO_FORCE", "TRANSPOSITION_DEADLINE", "REPEAL",
                    "COMMITTEE_PROCEDURE", "REPORTING_TO_COMMISSION", "AMENDMENT_POINTER",
                    "CORRELATION_TABLE", "HEADING_ONLY")
IGNORE_REASONS = ("FORMAL", "OUTSIDE_BUSINESS", "NO_MATCH", "NO_PATH")
NULLS = frozenset({NONE, NO_MATCH, *NULL_ALIASES})
LEVEL_PRIORITY = {"DIRECT": 3, "INDIRECT": 2, "UNSURE": 1, "IGNORE": 0}
TEXT_LIMITS = {"actor": 160, "subject": 200, "reason": 500}
QUESTION_CHARS = 200
THINKING_TOKENS = 4096

# Size limits in characters of JSON text. The target is never shortened: a
# target above TARGET_CHARS is split into parts that are each classified.
TARGET_CHARS = 12000
MIN_PART_CHARS = 1500
OPENING_CHARS = 500
TABLE_LEAD_CHARS = 1200
POSITION_CHARS, POSITION_ITEM_CHARS = 3000, 1500
REFERENCE_CHARS, REFERENCE_ITEM_CHARS = 5000, 1500
FOOTNOTE_CHARS, FOOTNOTE_ITEM_CHARS = 1500, 500
GOVERNING_TEXT_CHARS = 800
MAX_GOVERNING = 3
MAX_MISSING = 12
MAX_REQUESTS = 200
PASSAGE_WORDS = 35

SCORE_TASK = "TASK: Classify the target. Return the JSON object described in the instructions."
SOURCE_KEYS = {"source", "source_location", "source_id"}


class TargetScreeningError(ModelError):
    """A failed request or reply specific to a single provision."""

    def __init__(self, message, attempts=()):
        super().__init__(message)
        self.attempts = list(attempts)


class ContextLimitError(TargetScreeningError):
    """The model rejected the request because it exceeds its context window."""


def size(value):
    return len(json.dumps(value, ensure_ascii=False))


# --------------------------------------------------------------------- evidence pack

def build_pack(evidence, unit, supporting_ids=()):
    """Collect the complete evidence for one target. request_pack() limits what is sent."""
    key = unit["id"]
    kind = unit["kind"]
    included, note_ids = set(), list(unit["footnote_ids"])
    page_tables_in_text = []

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
        item = {"locator": evidence.locator(table_id), "caption": table["caption"], "rows": rows}
        if table.get("origin") == "PDF_PAGE_GRID":
            item["source_page"] = table["source"]["page"]
        return item

    def section_record(section_id):
        section = evidence.sections[section_id]
        included.add(section_id)
        blocks = []
        has_text = any((block.get("text") or "").strip() for block in section["blocks"])
        for block in section["blocks"]:
            note_ids.extend(block.get("footnote_ids", []))
            if table_id := block.get("table_id"):
                # A PDF page table repeats words that the stored annex text already holds.
                if has_text and evidence.tables[table_id].get("origin") == "PDF_PAGE_GRID":
                    page_tables_in_text.append(evidence.locator(table_id))
                    continue
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
        if recovery_source := source.get("recovery_source"):
            item["source_location"] = recovery_source
        return item

    provision = record(key)
    source_ids = list(dict.fromkeys([*unit["source_ids"], *sorted(included)]))
    context = []
    if kind == "TABLE":
        # Only the text just before the table. The whole section can be an entire annex.
        parent = evidence.tables[key]["section_id"]
        section = evidence.sections[parent]
        included.add(parent)
        before = []
        for block in section["blocks"]:
            if block.get("table_id") == key:
                break
            if block.get("text"):
                before.append(block["text"])
        lead = "\n".join(before).strip()
        item = {"locator": evidence.locator(parent), "relation": "text just before the target table"}
        if section["headings"]:
            item["headings"] = section["headings"]
        if lead:
            item["text"] = lead if len(lead) <= TABLE_LEAD_CHARS else "[…] " + lead[-TABLE_LEAD_CHARS:]
        if len(item) > 2:
            context.append(item)
    if unit["annex_id"] is not None:
        for section_id in evidence.section_order[unit["annex_id"]]:
            if (section_id not in included and norm(evidence.sections[section_id]["label"]) == "scope"
                    and evidence.sections[section_id]["blocks"]):
                context.append({**section_record(section_id), "relation": "scope of the annex"})

    # The provision that brings an annex or table into effect says whom it applies to.
    governing = {}
    for ref_id in unit.get("governing_article_reference_ids", ()):
        ref = evidence.refs[ref_id]
        source_id = ref["source_id"]
        if source_id in governing or source_id in source_ids or source_id not in evidence.elements:
            continue
        text = (evidence.elements[source_id].get("text") or "").strip()
        if len(text) > GOVERNING_TEXT_CHARS and ref.get("source_excerpt"):
            text = f"[…] {ref['source_excerpt']} […]"
        if text:
            governing[source_id] = {"locator": evidence.locator(source_id), "text": text,
                                    "relation": "provision that refers to the target"}
        if len(governing) == MAX_GOVERNING:
            break
    context.extend(governing.values())

    refs = {ref["id"]: ref for source_id in source_ids for ref in evidence.forward[source_id]}
    for ref_id in unit["forward_reference_ids"]:
        refs[ref_id] = evidence.refs[ref_id]
    # An entire annex contains its parts and tables, so include it first when cited.
    refs = sorted(refs.values(), key=lambda ref: {
        "ANNEX": 0, "ANNEX_PART": 1,
    }.get(evidence.elements.get(ref["target_id"], {}).get("kind"), 2))
    direct_refs, by_target, unavailable = [], {}, {}
    for ref in refs:
        if ref["status"] == "RESOLVED":
            cited_id = target_id = ref["target_id"]
            # Keep the full article. The cited paragraph is recorded for a short version.
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
                by_target[target_id] = record(target_id)
                direct_refs.append(by_target[target_id])
            if cited_id != target_id and target_id in by_target and cited_id in evidence.elements:
                text = (evidence.elements[cited_id].get("text") or "").strip()
                cited = by_target[target_id].setdefault("cited", [])
                locator = evidence.locator(cited_id)
                if text and all(item["locator"] != locator for item in cited):
                    cited.append({"locator": locator, "text": text})
        else:
            item, reason = evidence.retrieve(ref["id"], ref["target_instrument"])
            identity = (ref["target_instrument"], ref["target_locator"])
            if identity not in unavailable:
                if item is not None:
                    direct_refs.append(item)
                unavailable[identity] = reason
    context.extend(record(source_id) for source_id in supporting_ids if source_id not in included)
    document = {field: evidence.document[field] for field in ("celex", "version_id", "title")}
    if evidence.document.get("language") == "DE":
        document["language"] = "DE"
    if coverage := evidence.document.get("source_coverage"):
        document["source_coverage"] = coverage["limitations"]
    pack = {
        "document": document,
        "provision": provision,
        "direct_references": direct_refs,
        "essential_context": context,
        "footnotes": evidence.note_evidence(note_ids),
        "unavailable_references": [
            {"instrument": instrument, "locator": locator, "reason": reason}
            for (instrument, locator), reason in unavailable.items() if reason is not None
        ],
    }
    if page_tables_in_text:
        pack["page_tables_read_as_text"] = sorted(set(page_tables_in_text))
    return pack


def target_text(value):
    """Yield source text from a record, including table cells."""
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            yield value["text"]
        for child in value.values():
            yield from target_text(child)
    elif isinstance(value, list):
        for child in value:
            yield from target_text(child)


def number_passages(node):
    """Mark the target text with passage IDs so that it is sent only once.

    Running text gets a marker every PASSAGE_WORDS words. A table row gets one
    marker. Source bookkeeping fields are left out of the request.
    """
    passages = {}

    def mark(text):
        pieces = []
        for chunk in re.findall(r"\S+(?:\s+\S+){0,%d}" % (PASSAGE_WORDS - 1), text):
            key = f"P{len(passages) + 1}"
            passages[key] = chunk
            pieces.append(f"[{key}] {chunk}")
        return " ".join(pieces)

    def mark_row(row):
        text = " | ".join(cell["text"].strip() for cell in row if cell.get("text", "").strip())
        if not text:
            return row
        key = f"P{len(passages) + 1}"
        passages[key] = text
        cells, marked = [], False
        for cell in row:
            if not marked and cell.get("text", "").strip():
                cell, marked = {**cell, "text": f"[{key}] {cell['text']}"}, True
            cells.append(cell)
        return cells

    def walk(value):
        if isinstance(value, list):
            return [walk(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for field, child in value.items():
            if field in SOURCE_KEYS:
                continue
            if field == "text" and isinstance(child, str):
                result[field] = mark(child) if child.strip() else child
            elif field == "rows" and isinstance(child, list):
                result[field] = [mark_row(row) if isinstance(row, list) else walk(row) for row in child]
            else:
                result[field] = walk(child)
        return result

    return walk(node), passages


# --------------------------------------------------------------------- splitting

def split_text(text, limit):
    """Split text at the best legal boundary before the limit: paragraph, point, sentence."""
    boundaries = (r"\n+", r"\s+(?=\d{1,3}\.\s+\S)", r"\s+(?=\([a-z]{1,3}\)\s)", r"(?<=[.;:])\s+", r"\s+")
    parts, start = [], 0
    while len(text) - start > limit:
        window = text[start:start + limit]
        cut = None
        for pattern in boundaries:
            ends = [match.end() for match in re.finditer(pattern, window) if match.end() > limit // 2]
            if ends:
                cut = ends[-1]
                break
        cut = cut or limit
        parts.append(text[start:start + cut].strip())
        start += cut
    parts.append(text[start:].strip())
    return [part for part in parts if part]


def split_rows(rows, budget):
    """Group table rows. Header rows are repeated and a row span is never cut."""
    headers = []
    for row in rows:
        if not row or not all(cell.get("header") for cell in row):
            break
        headers.append(row)
    body = rows[len(headers):]
    if not body:
        return [rows]
    blocked = {cut for index, row in enumerate(body) for cell in row
               for cut in range(index + 1, min(len(body), index + cell.get("rowspan", 1)))}
    room = max(budget - size(headers), MIN_PART_CHARS)
    groups, group, used = [], [], 0
    for index, row in enumerate(body):
        length = size(row) + 2  # list separator
        if group and used + length > room and index not in blocked:
            groups.append(headers + group)
            group, used = [], 0
        group.append(row)
        used += length
    groups.append(headers + group)
    return groups


def split_target(node, budget):
    """Split a target into parts of at most `budget` characters without dropping text."""
    if size(node) <= budget:
        return [node]
    for field in ("sections", "blocks"):
        children = node.get(field)
        if not children:
            continue
        shell = size({**node, field: []})
        room = max(budget - shell, MIN_PART_CHARS)
        parts, group, used = [], [], 0
        for child in children:
            length = size(child) + 2  # list separator
            if length > room:
                if group:
                    parts.append({**node, field: group})
                    group, used = [], 0
                parts.extend({**node, field: [piece]} for piece in split_target(child, room))
                continue
            if group and used + length > room:
                parts.append({**node, field: group})
                group, used = [], 0
            group.append(child)
            used += length
        if group:
            parts.append({**node, field: group})
        return parts
    if isinstance(node.get("table"), dict):
        room = max(budget - size({**node, "table": {}}), MIN_PART_CHARS)
        return [{**node, "table": part} for part in split_target(node["table"], room)]
    if node.get("rows"):
        room = max(budget - size({**node, "rows": []}), MIN_PART_CHARS)
        return [{**node, "rows": rows} for rows in split_rows(node["rows"], room)]
    text = node.get("text")
    if isinstance(text, str) and len(text) > MIN_PART_CHARS:
        room = max(budget - size({**node, "text": ""}), MIN_PART_CHARS)
        return [{**node, "text": piece} for piece in split_text(text, room)]
    return [node]


# --------------------------------------------------------------------- request

def plain(record):
    cited = record.get("cited") if isinstance(record, dict) else None
    if cited:
        return "\n".join(f"{item['locator']}: {item['text']}" for item in cited)
    return "\n".join(text for text in target_text(record) if text.strip())


def clean(value):
    """Drop source bookkeeping and the helper list of cited paragraphs."""
    if isinstance(value, list):
        return [clean(item) for item in value]
    if isinstance(value, dict):
        return {field: clean(child) for field, child in value.items()
                if field not in SOURCE_KEYS and field != "cited"}
    return value


def fit(records, total, per_item):
    """Keep records within a character budget. Long ones become excerpts."""
    kept, left_out, remaining = [], [], total
    for record in records:
        limit = min(per_item, remaining)
        item = clean(record)
        if size(item) > limit:
            text = plain(record)
            room = limit - 160
            if room < 200 or not text:
                left_out.append(str(record.get("locator", "unnamed")))
                continue
            item = {field: record[field] for field in ("locator", "relation", "heading") if record.get(field)}
            item.update(text=text if len(text) <= room else text[:room].rstrip() + " […]", excerpt=True)
        kept.append(item)
        remaining -= size(item)
    return kept, left_out


def request_pack(pack, part, part_label=None, opening=None, act_context=None, warnings=()):
    """Build what is sent for one target part: the target once, and limited context."""
    target, passages = number_passages(part)
    if part_label:
        target["part"] = part_label
    document = pack["document"]
    act = {"celex": document["celex"], "title": document["title"]}
    if document.get("language"):
        act["language"] = document["language"]
    if act_context:
        act["document_screening"] = act_context
    notes = [*warnings, *document.get("source_coverage", ())]
    if part_label:
        notes.append("The target is long and is classified in parts. This request holds one part.")

    position = list(pack["essential_context"])
    if opening:
        position.insert(0, {"locator": pack["provision"]["locator"], "text": opening,
                            "relation": "opening words of the whole target"})
    position, position_out = fit(position, POSITION_CHARS, POSITION_ITEM_CHARS)
    references, references_out = fit(pack["direct_references"], REFERENCE_CHARS, REFERENCE_ITEM_CHARS)
    footnotes, footnotes_out = fit(pack["footnotes"], FOOTNOTE_CHARS, FOOTNOTE_ITEM_CHARS)
    missing = pack["unavailable_references"]
    sent = {"act": act}
    if notes:
        sent["source_notes"] = notes
    sent["target"] = target
    if position:
        sent["position"] = position
    if references:
        sent["references"] = references
    if left_out := [*position_out, *references_out, *footnotes_out]:
        sent["context_not_included"] = left_out[:MAX_MISSING]
    if footnotes:
        sent["footnotes"] = footnotes
    if missing:
        sent["missing_references"] = missing[:MAX_MISSING]
    trimmed = {
        "context_left_out": len(left_out),
        "excerpts": sum(bool(item.get("excerpt")) for item in [*position, *references, *footnotes]),
        "missing_references_listed": min(len(missing), MAX_MISSING), "missing_references": len(missing),
    }
    return sent, passages, trimmed


def reply_schema(passages, options):
    text = {name: {"type": "string", "minLength": 1, "maxLength": limit} for name, limit in TEXT_LIMITS.items()}
    return {
        "type": "object", "additionalProperties": False, "required": list(REPLY_FIELDS),
        "properties": {
            "actor": text["actor"],
            "actor_kind": {"type": "string", "enum": list(ACTOR_KINDS)},
            "subject": text["subject"],
            "target_passage": {"type": "string", "enum": list(passages)},
            "formal_function": {"type": "string", "enum": [*FORMAL_FUNCTIONS, *NULL_ALIASES]},
            "topic": {"type": "string", "enum": options["topics"]},
            "other_anchors": {"type": "array", "maxItems": 2,
                              "items": {"type": "string", "enum": options["anchors"]}},
            "company_role": {"type": "string", "enum": options["roles"]},
            "indirect_path": {"type": "string", "enum": options["indirect_paths"]},
            "open_question": {"type": "string", "maxLength": QUESTION_CHARS},
            "reason": text["reason"],
        },
    }


def normalise(reply):
    """Map every spelling of "no value" to one value per field."""
    def value(name, null):
        return null if reply[name] in NULLS else reply[name]
    return {**reply, "topic": value("topic", NO_MATCH), "company_role": value("company_role", NONE),
            "indirect_path": value("indirect_path", NONE), "formal_function": value("formal_function", NONE)}


def derive(reply, options):
    """Work out (level, basis) from the model's normalised answers."""
    topic, role, path = reply["topic"], reply["company_role"], reply["indirect_path"]
    question = reply["open_question"].strip()
    business_side = reply["actor_kind"] in (BUSINESS, NOT_STATED)
    if reply["formal_function"] != NONE:
        # A formal clause has no business actor. If the reply names one, a person decides.
        return ("UNSURE", "FORMAL_CONFLICT") if reply["actor_kind"] == BUSINESS else ("IGNORE", "FORMAL")
    if topic in options["negative_anchors"]:
        return "IGNORE", "OUTSIDE_BUSINESS"
    if topic == NO_MATCH:
        # A link was named although no catalog entry describes the subject: keep it for a person to check.
        return ("UNSURE", "LINK_WITHOUT_TOPIC") if role != NONE or path != NONE else ("IGNORE", "NO_MATCH")
    if role != NONE:
        if business_side:
            return ("UNSURE", "OPEN_QUESTION") if question else ("DIRECT", "ROLE")
        # A role was named although the actor is a state, an EU body or a private person.
        return ("INDIRECT", "PATH") if path != NONE else ("UNSURE", "ROLE_ACTOR_CONFLICT")
    if path != NONE:
        return "INDIRECT", "PATH"
    if question:
        return "UNSURE", "OPEN_QUESTION"
    # The subject lies in a business area and a business is the actor, but no link was named:
    # too uncertain to remove.
    return ("UNSURE", "TOPIC_ONLY") if business_side else ("IGNORE", "NO_PATH")


def validate_decision(reply, passages, options):
    """Check the reply's form and derive the level. Returns (decision, details)."""
    if not isinstance(reply, dict) or set(reply) != set(REPLY_FIELDS):
        raise ModelError("Return exactly these fields: " + ", ".join(REPLY_FIELDS) + ".")
    for name, limit in TEXT_LIMITS.items():
        if not isinstance(reply[name], str) or not 0 < len(reply[name].strip()) <= limit:
            raise ModelError(f"Expected a non-empty {name} of at most {limit} characters.")
    passage = reply["target_passage"]
    if not isinstance(passage, str) or passage not in passages:
        raise ModelError("Select a target_passage ID that is marked in the target.")
    if (reply["actor_kind"] not in ACTOR_KINDS or reply["formal_function"] not in (*FORMAL_FUNCTIONS, *NULL_ALIASES)
            or reply["topic"] not in options["topics"] or reply["company_role"] not in options["roles"]
            or reply["indirect_path"] not in options["indirect_paths"]):
        raise ModelError("Use the listed values for actor_kind, formal_function, topic, company_role "
                         "and indirect_path.")
    others, question = reply["other_anchors"], reply["open_question"]
    if (not isinstance(others, list) or len(others) > 2
            or any(anchor not in options["anchors"] for anchor in others)):
        raise ModelError("other_anchors holds up to two business anchor IDs from the company profile.")
    if not isinstance(question, str) or len(question) > QUESTION_CHARS:
        raise ModelError(f"open_question is a string of at most {QUESTION_CHARS} characters.")

    reply = normalise(reply)
    level, basis = derive(reply, options)
    topic, question, reason = reply["topic"], question.strip(), reply["reason"].strip()
    kept = level != "IGNORE"
    area = []
    if kept:
        area = list(dict.fromkeys([*([topic] if topic in options["anchors"] else []), *others]))
        if level == "UNSURE" and question:
            reason = f"{reason} To check: {question}"
    details = {
        "actor": reply["actor"].strip(), "actor_kind": reply["actor_kind"], "subject": reply["subject"].strip(),
        "target_passage": passage, "target_quote": passages[passage],
        "formal_function": reply["formal_function"], "topic": topic,
        "company_role": reply["company_role"], "indirect_path": reply["indirect_path"],
        "open_question": question, "other_anchors": list(dict.fromkeys(others)),
        "basis": basis, "ignore_reason": NONE if kept else basis,
        "negative_anchor": topic if topic in options["negative_anchors"] else NONE,
    }
    return {"level": level, "reason": reason[:700], "area": area}, details


def cached_tokens(usage):
    details = (usage or {}).get("prompt_tokens_details") or {}
    return details.get("cached_tokens")


def score_request(client, system, sent, passages, options, task, log, thinking=False):
    """One classification with at most one correction. Returns (decision, audit)."""
    if not passages:
        raise TargetScreeningError("The target has no text available for a passage citation.")
    schema = reply_schema(passages, options)
    evidence_text = json.dumps(sent, ensure_ascii=False)
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": f"{evidence_text}\n\n{task}"}]
    rejected_reply = None
    for attempt in range(2):
        if len(log) >= MAX_REQUESTS:
            raise TargetScreeningError("The target reached its request limit.")
        if thinking:
            # The client applies its sampling settings for thinking. Thinking text counts as output.
            reply = client.chat(messages, thinking=True, stream=False, seed=42, schema=schema,
                                schema_name="provision_relevance", max_tokens=THINKING_TOKENS)
        else:
            reply = client.chat(messages, thinking=False, stream=False, seed=42, sampling={"temperature": 0},
                                schema=schema, schema_name="provision_relevance", max_tokens=1024)
        log.append({"seconds": round(getattr(reply, "seconds", 0.0) or 0.0, 3),
                    "prompt_tokens": (reply.usage or {}).get("prompt_tokens"),
                    "cached_tokens": cached_tokens(reply.usage),
                    "completion_tokens": (reply.usage or {}).get("completion_tokens")})
        if not reply.ok:
            if reply.error_kind == "context_limit":
                raise ContextLimitError("The request exceeds the model context limit.")
            raise ModelError(client.clean(reply.error or "Model request failed."))
        candidate, error = reply.parse_json()
        try:
            if error:
                raise ModelError(error)
            decision, details = validate_decision(candidate, passages, options)
        except ModelError as exc:
            rejected = {"raw_reply": reply.content, "error": str(exc), "usage": reply.usage,
                        "finish_reason": reply.finish_reason}
            if attempt:
                raise TargetScreeningError(
                    f"Invalid response after one correction request: {exc}", [rejected_reply, rejected]
                ) from exc
            rejected_reply = rejected
            # The correction goes last so that the shared start of the request stays cached.
            messages[1]["content"] = (f"{evidence_text}\n\n{task}\n\nCORRECTION: your previous reply was "
                                      f"rejected. {exc} Return the complete JSON object again.")
        else:
            audit = {"finish_reason": reply.finish_reason, "usage": reply.usage,
                     "raw_reply": reply.content, "evidence": details}
            if getattr(reply, "reasoning", ""):
                audit["thinking"] = reply.reasoning[:6000]
            if rejected_reply is not None:
                audit["rejected_reply"] = rejected_reply
            return decision, audit


# The confirmation pass does not see the first answers, so that it is a second opinion.
REVIEW_TASK = ("TASK: Confirmation pass. A first pass found no reason to keep this target. Answer all "
               "questions again from the text of the target, as described under CONFIRMATION PASS, and "
               "return the JSON object.")


def screen(client, system, profile, pack, first=None, act_context=None, warnings=(), thinking=False):
    """Classify a target, in parts when it is long.

    With `first` (the audit of an initial IGNORE) every part gets the
    confirmation task. The target takes the highest level of its parts.
    Returns (decision, audit).
    """
    options = choices(profile)
    provision = pack["provision"]
    top = split_target(provision, TARGET_CHARS)
    opening = " ".join(" ".join(target_text(provision)).split())[:OPENING_CHARS]
    results, log = [], []

    def run(part, part_id, total, budget):
        label = f"{part_id} of {total}" if total > 1 or "." in part_id else None
        sent, passages, trimmed = request_pack(
            pack, part, label, opening if label and part_id != "1" else None, act_context, warnings)
        task = REVIEW_TASK if first else SCORE_TASK
        try:
            decision, audit = score_request(client, system, sent, passages, options, task, log, thinking)
        except ContextLimitError:
            smaller = split_target(part, max(budget // 2, MIN_PART_CHARS))
            if len(smaller) < 2:
                raise ContextLimitError(
                    f"Part {part_id} of {provision['locator']} cannot be made small enough.") from None
            for index, piece in enumerate(smaller, 1):
                run(piece, f"{part_id}.{index}", total, max(budget // 2, MIN_PART_CHARS))
            return
        results.append({"part": part_id, "target_chars": size(part), "decision": decision,
                        "context": trimmed, **audit})

    for index, part in enumerate(top, 1):
        run(part, str(index), len(top), TARGET_CHARS)

    winner = max(results, key=lambda item: LEVEL_PRIORITY[item["decision"]["level"]])
    decision = dict(winner["decision"])
    if len(results) > 1:
        relevant = [item["part"] for item in results if item["decision"]["level"] != "IGNORE"]
        lead = (f"Classified in {len(results)} parts; relevant: part {', '.join(relevant)}. " if relevant
                else f"Classified in {len(results)} parts; none is relevant. ")
        decision["reason"] = (lead + decision["reason"])[:700]
        decision["area"] = list(dict.fromkeys(
            anchor for item in results if item["decision"]["level"] == decision["level"]
            for anchor in item["decision"]["area"]))[:6]
    audit = {"finish_reason": winner["finish_reason"], "usage": winner["usage"],
             "evidence": winner["evidence"], "parts": results, "requests": log}
    return decision, audit
