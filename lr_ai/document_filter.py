"""Qwen decides. Python prepares inputs, makes requests and saves outputs."""
from datetime import date
from pathlib import Path
import json
import re

LEVELS = ["DIRECT", "INDIRECT", "IGNORE"]
FIRST_OUTPUT_TOKENS = 6000
RETRY_OUTPUT_TOKENS = 6000
SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string", "maxLength": 600},
        "anchor_ids": {"type": "array", "maxItems": 3,
                       "items": {"type": "string", "maxLength": 100}},
        "level": {"type": "string", "enum": LEVELS},
    },
    "required": ["reason", "anchor_ids", "level"],
    "additionalProperties": False,
}

DECISION_PROMPT = (Path(__file__).resolve().parents[1] / "prompts" / "document_relevance.txt").read_text(encoding="utf-8").strip()

SECOND_PASS_PROMPT = (Path(__file__).resolve().parents[1] / "prompts" / "second_pass.txt").read_text(encoding="utf-8").strip()
REVIEW_IGNORE_PROMPT = (Path(__file__).resolve().parents[1] / "prompts" / "review_ignore.txt").read_text(encoding="utf-8").strip()

STOP = set("the and for with from this that shall must article regulation directive "
           "which have has are its into under such their der die das und von mit "
           "eine einer einem den des dem für auf ist sind".split())


def words(text):
    return {w for w in re.findall(r"[^\W\d_]{3,}", text.casefold()) if w not in STOP}


def split_text(text, max_chars=2800):
    """Keep every character, prefer a sentence/paragraph boundary to a hard split."""
    remaining = text.strip()
    while remaining:
        end = len(remaining)
        if end > max_chars:
            candidates = [m.end() for m in re.finditer(r"(?:[.!?]\s+|\n\s*\n)", remaining[:max_chars])]
            end = max((n for n in candidates if n >= max_chars // 2), default=max_chars)
        yield remaining[:end]
        remaining = remaining[end:]


def prepare(act, as_of):
    version = act["version"]
    problems = []          # stop: nothing usable to screen
    warnings = act.setdefault("input_warnings", [])   # noted, screening continues
    for field in ("version_date", "effective_from"):
        value = version.get(field)
        if value and date.fromisoformat(value[:10]) > as_of:
            note = f"Stored text is a future consolidated version ({field} {value[:10]}); screened as published"
            if note not in warnings:
                warnings.append(note)
    document = act.get("document") or {}
    if document.get("document_date") and date.fromisoformat(document["document_date"][:10]) > as_of:
        problems.append("Act document date is after the assessment date")
    elements = act.get("elements")
    if not isinstance(elements, list):
        raise ValueError("Act has no elements array")
    articles = [e for e in elements if e.get("element_type") == "ARTICLE"]
    if not articles and not any(e.get("body_text", "").strip() for e in elements):
        problems.append("No stored source text")
    if any("~duplicate" in e.get("path", "") for e in articles):
        note = "Parser produced duplicate article boundaries; identical repeats are skipped"
        if note not in warnings:
            warnings.append(note)
    if version.get("quality_status") == "REJECTED":
        problems.append("Stored version is rejected")
    # Aggregate ARTICLE/ANNEX rows already contain their children in the sample.
    # Keep these sources once. If aggregation is absent, include descendant bodies.
    blocks, outline, seen_texts = [], [], set()
    for element in elements:
        typ = element.get("element_type")
        if typ not in {"TITLE", "PART", "CHAPTER", "SECTION", "ARTICLE", "ANNEX", "RECITAL", "DOCUMENT_TEXT"}:
            continue
        heading = element.get("heading") or ""
        path = element["path"]
        if typ in {"TITLE", "PART", "CHAPTER", "SECTION"}:
            outline.append({"path": path, "type": typ,
                            "heading": heading or element.get("body_text", "")})
            continue
        text = element.get("body_text") or ""
        if typ in {"ARTICLE", "ANNEX"} and not element.get("details", {}).get("aggregate_body"):
            descendants = [e.get("body_text") or "" for e in elements
                           if e["path"].startswith(path + "/")]
            # Avoid duplicating child text already stored in the container.
            extras = [s for s in descendants if s.strip() and s not in text]
            if extras:
                text += "\n" + "\n".join(extras)
        if typ in {"ARTICLE", "ANNEX"}:
            fingerprint = (typ, element.get("number") or re.sub(r"~duplicate.*$", "", path), " ".join(text.split()))
            if fingerprint in seen_texts:          # exact repeat from a duplicate boundary
                continue
            seen_texts.add(fingerprint)
        ids = []
        for index, part in enumerate(split_text(text), 1):
            source_id = f"{path}#chunk{index}"
            ids.append(source_id)
            blocks.append({"source_id": source_id, "path": path, "type": typ,
                           "heading": heading, "text": part,
                           "citation_verified": False if any("~duplicate" in e.get("path", "") for e in articles) else None,
                           "boundary_warning": "Parser reported duplicate boundaries; stored article labels require verification" if any("~duplicate" in e.get("path", "") for e in articles) else None})
        outline.append({"path": path, "type": typ, "heading": heading,
                        "source_ids": ids})
    # Recitals may live only in recital_sets in some database versions.
    if not any(b["type"] == "RECITAL" for b in blocks):
        recital_set = act.get("recital_set") or {}
        for index, recital in enumerate(recital_set.get("recitals_json") or [], 1):
            if not isinstance(recital, dict):
                problems.append("Unsupported recital entry format")
                continue
            text = recital.get("text") or ""
            for chunk, part in enumerate(split_text(text), 1):
                blocks.append({"source_id": f"recital-set/{index}#chunk{chunk}",
                               "path": f"recital-set/{index}", "type": "RECITAL",
                               "heading": str(recital.get("n", index)), "text": part})
    for block in blocks:
        if not any(block["source_id"] in o.get("source_ids", []) for o in outline):
            outline.append({"path": block["path"], "type": block["type"],
                            "heading": block["heading"], "source_ids": [block["source_id"]]})
    for block in blocks:
        block["source_version"] = (
            act.get("recitals_version") or version.get("version_key") or version.get("id")
        ) if block["type"] == "RECITAL" else version.get("version_key") or version.get("id")
    if not blocks and elements:
        # Older exports may only contain paragraphs/points/preamble bodies.
        for i, element in enumerate(elements):
            for j, part in enumerate(split_text(element.get("body_text") or ""), 1):
                blocks.append({"source_id": f"fallback/{i}#chunk{j}",
                               "path": element.get("path", f"row/{i}"),
                               "type": "DOCUMENT_TEXT", "heading": element.get("heading") or "",
                               "text": part, "source_version": version.get("id")})
    if not blocks and "No stored source text" not in problems:
        problems.append("No usable stored legal text")
    for issue in problems:
        if issue not in warnings:
            warnings.append(issue)
    covered_paths = {b['path'] for b in blocks if b['type'] in ('ARTICLE', 'ANNEX', 'RECITAL', 'DOCUMENT_TEXT')}
    for index, element in enumerate(elements):
        path = element.get('path', f'unstructured/{index}')
        if element.get('element_type') in {'TITLE', 'PART', 'CHAPTER', 'SECTION'}:
            continue
        if any(path == known or path.startswith(known + '/') for known in covered_paths):
            continue
        for chunk, text in enumerate(split_text(element.get('body_text') or ''), 1):
            blocks.append({'source_id': f'unstructured/{index}#chunk{chunk}', 'path': path,
                           'type': 'DOCUMENT_TEXT', 'heading': '', 'text': text,
                           'source_version': version.get('id')})
    if blocks:
        problems = [p for p in problems if p not in ('No stored source text', 'No usable stored legal text')]
    return blocks, outline, problems


def spread_order(items):
    """Opening text then successively sample the widest remaining gaps."""
    if not items:
        return []
    indices = [0]
    if len(items) > 1:
        indices.append(1)
    remaining = set(range(len(items))) - set(indices)
    while remaining and len(indices) < 32:
        index = max(remaining, key=lambda i: (min(abs(i-j) for j in indices), -i))
        indices.append(index)
        remaining.remove(index)
    return [items[i] for i in indices]


def initial_selection(blocks, profile):
    # Source order and document coverage only: no business words or heading scores.
    groups = [spread_order([b for b in blocks if b['type'] in ('ARTICLE', 'DOCUMENT_TEXT')]),
              spread_order([b for b in blocks if b['type'] == 'RECITAL']),
              spread_order([b for b in blocks if b['type'] == 'ANNEX'])]
    selected = []
    for offset in range(max((len(g) for g in groups), default=0)):
        for group in groups:
            if offset < len(group):
                selected.append(group[offset]['source_id'])
    return list(dict.fromkeys(selected))


def coverage(blocks, selected):
    operative = {b["source_id"] for b in blocks if b["type"] in {"ARTICLE", "ANNEX"}}
    return {"operative_chunks_total": len(operative),
            "operative_chunks_seen": len(operative & set(selected)),
            "all_operative_chunks_seen": bool(operative) and operative <= set(selected)}


MAX_OUTLINE_ENTRIES = 150
MAX_HEADING_CHARS = 120


def compact_outline(outline):
    """Headings without recitals or chunk IDs, capped so a very long act still fits.

    47 of 13,962 exported acts have more than 150 entries (the largest 2,205), which
    alone would exceed any input budget.
    """
    entries = [{"path": o["path"], "type": o["type"], "heading": (o["heading"] or "")[:MAX_HEADING_CHARS]}
               for o in outline if o["type"] != "RECITAL"]
    if len(entries) <= MAX_OUTLINE_ENTRIES:
        return entries
    omitted = len(entries) - MAX_OUTLINE_ENTRIES
    return entries[:MAX_OUTLINE_ENTRIES] + [
        {"path": "", "type": "NOTE", "heading": f"{omitted} further outline entries omitted"}]



def assess_version(act, as_of):
    version = act.get("version") or {}
    dates = {k: version.get(k) for k in ("version_date", "effective_from")}
    future = any(v and date.fromisoformat(v[:10]) > as_of for v in dates.values())
    return {"as_of": as_of.isoformat(), "version_id": version.get("id"),
            "version_key": version.get("version_key"), **dates,
            "future_version": bool(future),
            "assessment_scope": "potential_future_relevance" if future else "document_relevance",
            "current_applicability_verified": False}

class RequestError(RuntimeError):
    """Technical failure, never a relevance label."""

    def __init__(self, message, details=None, shown=None):
        super().__init__(message)
        self.details = details or {}
        self.shown = shown or []


def request(client, prompt, payload, context_limit, *, thinking, output_tokens,
            input_budget, diagnostics, fit_prompts=()):
    payload = dict(payload)
    payload['excerpts'] = list(payload['excerpts'])
    while True:
        messages = [{'role': 'system', 'content': prompt},
                    {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]
        count = client.tokenize(messages=messages)
        if count is None:
            raise RequestError('Server tokenizer unavailable')
        counts = [count]
        for other in fit_prompts:
            other_count = client.tokenize(messages=[{'role': 'system', 'content': other}, messages[1]])
            if other_count is None:
                raise RequestError('Server tokenizer unavailable for review budget')
            counts.append(other_count)
        if max(counts) <= min(input_budget, context_limit-output_tokens-512):
            break
        if len(payload['excerpts']) <= 1:
            raise RequestError('Request exceeds context budget')
        payload['excerpts'].pop()
    diagnostics.update(input_tokens=count, output_token_limit=output_tokens,
                       thinking_enabled=thinking,
                       selected_source_ids=[b['source_id'] for b in payload['excerpts']])
    if fit_prompts:
        diagnostics['reserved_prompt_input_tokens'] = counts[1:]
    schema = json.loads(json.dumps(SCHEMA))
    business = payload.get('business_profile') or {}
    ids = business.get('valid_anchor_ids') or list(business.get('anchors') or {})
    if ids:
        schema['properties']['anchor_ids']['items']['enum'] = ids
    response = client.chat(messages, thinking=thinking, stream=False, seed=42,
                           sampling={'temperature': 0}, schema=schema,
                           schema_name='document_relevance', max_tokens=output_tokens)
    # These are HTTP/decoding errors only; no legal, quote, role or anchor validation.
    details = {'raw_content': getattr(response, 'content', ''),
               'finish_reason': getattr(response, 'finish_reason', None),
               'reasoning_chars': len(getattr(response, 'reasoning', '') or ''),
               'completion_tokens': getattr(response, 'completion_tokens', None)}
    diagnostics.update(details)
    if not getattr(response, 'ok', True):
        raise RequestError(client.clean(str(getattr(response, 'error', 'Request failed'))), details, payload['excerpts'])
    if details['finish_reason'] == 'length':
        raise RequestError('Model answer was cut off', details, payload['excerpts'])
    value, error = response.parse_json()
    if error:
        raise RequestError(client.clean(str(error)), details, payload['excerpts'])
    # Response-format check only. Never judge relevance, roles, quotes or anchors.
    if not isinstance(value, dict) or set(value) != {'reason', 'anchor_ids', 'level'}:
        raise RequestError('Model returned an invalid decision object', details, payload['excerpts'])
    if value.get('level') not in LEVELS or not isinstance(value.get('reason'), str) or not isinstance(value.get('anchor_ids'), list):
        raise RequestError('Model returned an invalid decision format', details, payload['excerpts'])
    return value, payload['excerpts']


def legal_status_metadata(act):
    """Forward stored lifecycle fields, without guessing or assigning relevance."""
    keys = ('legal_status', 'in_force', 'is_in_force', 'valid_until',
            'date_end_validity', 'date_of_end_of_validity', 'end_of_validity',
            'repealed_by', 'repealed_by_celex')
    fields = {}
    for source, container in [('act', act)] + [(key, act.get(key)) for key in ('document', 'instrument', 'version')]:
        if not isinstance(container, dict):
            continue
        for key in keys:
            if key in container and container[key] is not None:
                fields[f'{source}.{key}'] = container[key]
    return {'available': bool(fields), 'source': 'stored_input_metadata',
            'independently_verified': False, 'fields': fields}


def run_act(client, act, profile, as_of, context_limit, max_chunks=24, input_budget=34000,
            review_ignore=True):
    blocks, outline, issues = prepare(act, as_of)
    version = assess_version(act, as_of)
    selected = initial_selection(blocks, profile)[:max_chunks]
    by_id = {b['source_id']: b for b in blocks}
    entry = {'round': 1, 'purpose': 'initial_screening'}
    rounds = [entry]
    payload = {
        'celex': act['celex'],
        'title': act['document'].get('title_en') or act['document'].get('title_de') or act['instrument'].get('title'),
        'business_profile': profile,
        'outline': compact_outline(outline),
        'excerpts': [by_id[s] for s in selected],
        'version_assessment': version,
        'legal_status_metadata': legal_status_metadata(act),
        'input_warnings': act.get('input_warnings', []),
        'data_issues': issues,
        'evidence_note': 'Selected passages sampled across the stored text, not the entire act unless all passages fit. Headings and numbering may be unreliable.',
    }
    shown = []
    first_decision = None
    verification = {'enabled': review_ignore, 'required': False, 'completed': False}
    review_prompt = DECISION_PROMPT + '\n\nIGNORE REVIEW\n' + REVIEW_IGNORE_PROMPT
    try:
        decision, shown = request(client, DECISION_PROMPT, payload, context_limit,
                                  thinking=True, output_tokens=FIRST_OUTPUT_TOKENS,
                                  input_budget=input_budget, diagnostics=entry,
                                  fit_prompts=(review_prompt,) if review_ignore else ())
        entry['candidate'] = decision
        first_decision = decision
        if review_ignore and decision['level'] == 'IGNORE':
            verification['required'] = True
            entry = {'round': 2, 'purpose': 'independent_ignore_review'}
            rounds.append(entry)
            # Re-send the actual first-pass evidence and full profile, not its
            # reason or hidden thinking. Reserve above keeps both packs identical.
            review_payload = dict(payload, excerpts=shown)
            decision, review_shown = request(client, review_prompt, review_payload, context_limit,
                                             thinking=True, output_tokens=RETRY_OUTPUT_TOKENS,
                                             input_budget=input_budget, diagnostics=entry)
            entry['candidate'] = decision
            entry['same_evidence_as_first_pass'] = [b['source_id'] for b in review_shown] == [b['source_id'] for b in shown]
            verification['completed'] = True
        return {'status': 'ok', 'decision': decision, 'first_pass_decision': first_decision,
                'verification': verification, 'rounds': rounds, 'evidence': shown,
                'data_warnings': act.get('input_warnings', []), 'data_issues': issues,
                'version_assessment': version, 'legal_status_metadata': payload['legal_status_metadata']}
    except Exception as exc:
        entry['request_error'] = client.clean(str(exc))
        if isinstance(exc, RequestError):
            if not shown:
                shown = exc.shown
        # A failed review is unresolved. Never silently use the first IGNORE as
        # a completed verified decision or turn an HTTP error into irrelevance.
        return {'status': 'failed', 'error': client.clean(str(exc)), 'rounds': rounds,
                'first_pass_decision': first_decision, 'verification': verification,
                'evidence': shown, 'data_warnings': act.get('input_warnings', []),
                'data_issues': issues, 'version_assessment': version,
                'legal_status_metadata': payload['legal_status_metadata']}
