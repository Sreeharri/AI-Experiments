"""Model request, output validation, bounded context retrieval and batch roll-up."""

import json

from .evidence import CONTEXT_CHAR_BUDGET, norm, size

LEVELS = ("IGNORE", "INDIRECT", "DIRECT")
FUNCTIONS = ("SCOPE", "DEFINITION", "OBLIGATION", "EXEMPTION", "TECHNICAL_REQUIREMENT",
             "PROCEDURE", "APPLICATION", "OTHER", "MIXED")
BRANCH_STATUSES = ("ASSESSED", "OUTSIDE_SCENARIO", "NEEDS_REVIEW")
REVIEW = ("COMPLETE", "NEEDS_REVIEW")
MAX_CONTEXT_ROUNDS = 2
MAX_CONTEXT_REQUESTS = 3
OUTPUT_TOKENS = 3200


class ModelError(RuntimeError):
    """A model or endpoint failure that can be retried by resuming the run."""


def object_schema(fields):
    return {"type": "object", "properties": fields, "required": list(fields),
            "additionalProperties": False}


def response_schema(anchors):
    string = {"type": "string"}
    strings = {"type": "array", "items": string}
    def enum(values):
        return {"enum": list(values)}
    decision = object_schema({
        "celex": string, "source_version_id": string, "target": string,
        "provision_function": enum(FUNCTIONS),
        "level": enum((*LEVELS, None)), "reason": string,
        "area": {"type": "array", "items": enum(anchors)}, "evidence_refs": strings,
        "review_status": enum(REVIEW), "missing_evidence": strings,
        "branch_results": {"type": "array", "items": object_schema({
            "locator": string, "status": enum(BRANCH_STATUSES),
            "reason": string, "evidence_refs": strings,
        })},
    })
    return object_schema({
        "result": {"anyOf": [decision, {"type": "null"}]},
        "context_requests": {"type": "array", "maxItems": MAX_CONTEXT_REQUESTS,
                             "items": object_schema({"locator": string, "instrument": string,
                                                     "why": string})},
    })


def prepare(evidence, unit, target, batch, common):
    context = evidence.initial(unit, batch)
    branches = []
    full = next((p for p in context["passages"] if p["relation"] == "FULL_TARGET_ARTICLE"), {})
    full_text = full.get("text", "")
    for branch in batch:
        item = {k: v for k, v in branch.items() if k != "source_ids"}
        if item.get("text") and item["locator"] != full.get("locator"):
            start = full_text.find(item["text"])
            if start >= 0:
                item["span_in_full_target_article"] = [start, start + len(item["text"])]
                item["target_excerpt"] = item.pop("text")[:100]
        branches.append(item)
    notes = gather_notes(evidence, branches + context["passages"])
    return {
        **common, "document": evidence.document, "target": target, "target_kind": unit["kind"],
        "target_heading": evidence.elements.get(unit["id"], {}).get("heading"),
        "branches": branches, "context": context, "footnotes": notes["available"],
        "missing_footnotes": notes["missing"], "retrieved_context": [],
        "retrieval_log": [], "can_request_context": True,
    }


def gather_notes(evidence, records):
    wanted = []
    for item in records:
        wanted.extend(item.get("note_ids", []))
        rows = item.get("rows", []) + item.get("header_and_spanning_rows", [])
        for row in rows:
            wanted.extend(row["note_ids"])
    return {"available": evidence.note_evidence(wanted),
            "missing": [key for key in dict.fromkeys(wanted) if key not in evidence.notes]}


def locators(payload):
    values = {branch["locator"] for branch in payload["branches"]}
    for item in (payload["context"]["passages"] + payload["retrieved_context"] + payload["footnotes"]):
        if item.get("text") or item.get("caption") or item.get("rows") or item.get("cells") or item.get("header_and_spanning_rows"):
            values.add(item["locator"])
        for row in item.get("rows", []) + item.get("header_and_spanning_rows", []):
            values.add(row["locator"])
    return values


def text(value):
    return isinstance(value, str) and bool(value.strip())


def strings(value):
    return isinstance(value, list) and all(text(item) for item in value)


def validate_answer(value, payload):
    if not isinstance(value, dict) or set(value) != {"result", "context_requests"}:
        raise ValueError("Expected result and context_requests.")
    result, requests = value["result"], value["context_requests"]
    if not isinstance(requests, list) or len(requests) > MAX_CONTEXT_REQUESTS:
        raise ValueError("Invalid context_requests list.")
    if requests:
        if result is not None:
            raise ValueError("Return null result when requesting context.")
        for request in requests:
            if (not isinstance(request, dict) or set(request) != {"locator", "instrument", "why"}
                    or not text(request["locator"]) or not text(request["why"])
                    or not isinstance(request["instrument"], str)):
                raise ValueError("Context requests need locator, instrument (empty if unknown), and why.")
        return
    required = {"celex", "source_version_id", "target", "provision_function", "level", "reason",
                "area", "evidence_refs", "review_status", "missing_evidence", "branch_results"}
    if not isinstance(result, dict) or set(result) != required:
        raise ValueError("Decision fields are missing or unexpected.")
    document = payload["document"]
    if (result["celex"], result["source_version_id"], result["target"]) != (
        document["celex"], document["version_id"], payload["target"]
    ):
        raise ValueError("Decision identity must match the target and pinned source.")
    if (result["provision_function"] not in FUNCTIONS or result["level"] not in (*LEVELS, None)
            or result["review_status"] not in REVIEW or not text(result["reason"])):
        raise ValueError("Invalid function, level, review status or reason.")
    areas = result["area"]
    if (not strings(areas) or not set(areas) <= payload["candidate_anchors"].keys()
            or len(set(areas)) != len(areas)
            or bool(areas) != (result["level"] in ("DIRECT", "INDIRECT"))):
        raise ValueError("Use supported anchor IDs and include areas only for DIRECT or INDIRECT.")
    missing = result["missing_evidence"]
    pending = result["review_status"] == "NEEDS_REVIEW"
    if (not strings(missing) or bool(missing) != pending
            or (result["level"] is None and not pending)
            or (pending and result["level"] not in (None, "DIRECT"))):
        raise ValueError("Material missing evidence requires NEEDS_REVIEW; use null unless DIRECT is established.")

    expected = {branch["locator"] for branch in payload["branches"]}
    available = locators(payload)
    citations = result["evidence_refs"]
    if not strings(citations) or not set(citations) <= available or not set(citations) & expected:
        raise ValueError("Decision must cite an exact supplied target branch locator.")
    if not isinstance(result["branch_results"], list):
        raise ValueError("branch_results must be a list.")
    seen, branch_pending = set(), False
    for branch in result["branch_results"]:
        if not isinstance(branch, dict) or set(branch) != {"locator", "status", "reason", "evidence_refs"}:
            raise ValueError("Invalid branch result fields.")
        locator, refs = branch["locator"], branch["evidence_refs"]
        if (not text(locator) or locator not in expected or locator in seen or branch["status"] not in BRANCH_STATUSES
                or not text(branch["reason"]) or not strings(refs) or locator not in refs
                or not set(refs) <= available):
            raise ValueError("Each branch must appear once and cite its own supplied locator.")
        branch_pending |= branch["status"] == "NEEDS_REVIEW"
        seen.add(locator)
    if seen != expected or (branch_pending and not pending):
        raise ValueError("Return all listed branches and propagate NEEDS_REVIEW.")
    if all(b["status"] == "OUTSIDE_SCENARIO" for b in result["branch_results"]) and result["level"] not in ("IGNORE", None):
        raise ValueError("Outside-scenario branches cannot establish DIRECT or INDIRECT.")


def request(client, prompt, payload):
    schema = response_schema(payload["candidate_anchors"])
    messages = [{"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    for attempt in range(2):
        response = client.chat(messages, thinking=False, stream=False, seed=42,
                               sampling={"temperature": 0}, schema=schema,
                               schema_name="provision_relevance", max_tokens=OUTPUT_TOKENS)
        value, error = response.parse_json()
        if not response.ok:
            raise ModelError(f"Qwen request failed ({response.error_kind}): {client.clean(response.error or error or '')}")
        try:
            if error:
                raise ValueError(error)
            validate_answer(value, payload)
            return value
        except ValueError as exc:
            if attempt:
                raise ModelError(f"Qwen response failed validation twice: {exc}") from exc
            messages[1]["content"] = json.dumps({
                **payload, "response_validation_feedback": str(exc),
            }, ensure_ascii=False)


def add_requested_context(evidence, payload, requests):
    added = 0
    known = locators(payload) | {item["locator"] for item in payload["retrieved_context"]}
    for request_data in requests:
        locator = request_data["locator"]
        record, problem = evidence.retrieve(locator, request_data["instrument"])
        entry = {**request_data, "status": "UNAVAILABLE"}
        if record is not None:
            if record["locator"] in known:
                problem = "This passage is already in the evidence."
            elif size(payload["retrieved_context"]) + size(record) > CONTEXT_CHAR_BUDGET:
                problem = "Passage is too large; request an exact paragraph, annex section or table row."
            else:
                payload["retrieved_context"].append(record)
                known.add(record["locator"])
                entry.update(status="RETRIEVED", resolved_locator=record["locator"])
                added += 1
        if problem:
            entry["detail"] = problem
        payload["retrieval_log"].append(entry)
    notes = gather_notes(evidence, payload["branches"] + payload["context"]["passages"]
                         + payload["retrieved_context"])
    payload.update(footnotes=notes["available"], missing_footnotes=notes["missing"])
    return added


def pending_result(payload, reason, issues):
    document = payload["document"]
    branches = [
        {"locator": b["locator"], "status": "NEEDS_REVIEW",
         "reason": reason,
         "evidence_refs": [b["locator"]]}
        for b in payload["branches"]
    ]
    return {
        "celex": document["celex"], "source_version_id": document["version_id"],
        "target": payload["target"], "provision_function": "OTHER", "level": None,
        "reason": reason,
        "area": [], "evidence_refs": [b["locator"] for b in branches],
        "review_status": "NEEDS_REVIEW", "missing_evidence": issues, "branch_results": branches,
    }


def score_batch(client, prompt, evidence, payload):
    for round_index in range(MAX_CONTEXT_ROUNDS + 1):
        answer = request(client, prompt, payload)
        if answer["result"] is not None:
            return answer["result"], payload["retrieval_log"]
        requests = answer["context_requests"]
        if not payload["can_request_context"]:
            payload["retrieval_log"].extend({**r, "status": "BUDGET_REACHED"} for r in requests)
            missing = [f"{r['instrument']} {r['locator']}: {r['why']}".strip() for r in requests]
            reason = "The requested context remained open when the retrieval budget was reached."
            return pending_result(payload, reason, missing), payload["retrieval_log"]
        added = add_requested_context(evidence, payload, requests)
        # Allow a final decision after the last round, even when retrieval did not succeed.
        payload["can_request_context"] = round_index + 1 < MAX_CONTEXT_ROUNDS and (
            added > 0 or any("too large" in r.get("detail", "") for r in payload["retrieval_log"])
        )
    raise AssertionError("Context loop did not reach a final decision.")


def review_draft(client, prompt, payload, draft):
    schema = object_schema({
        "issues": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
    })
    review_input = {k: v for k, v in payload.items() if k != "scenario_review_feedback"}
    review_input["draft"] = draft
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(review_input, ensure_ascii=False)},
    ]
    response = client.chat(
        messages, thinking=False, stream=False, seed=42, sampling={"temperature": 0},
        schema=schema, schema_name="provision_scenario_review", max_tokens=600,
    )
    value, error = response.parse_json()
    if not response.ok:
        raise ModelError(f"Qwen scenario check failed ({response.error_kind}): "
                         f"{client.clean(response.error or error or '')}")
    if error or not isinstance(value, dict) or set(value) != {"issues"}:
        raise ModelError(f"Invalid Qwen scenario check: {error or 'expected an issues array'}")
    issues = value["issues"]
    if not strings(issues) or len(issues) > 4:
        raise ModelError("Invalid Qwen scenario check: expected up to four nonempty issues.")
    return issues


def score_verified_batch(client, prompt, review_prompt, evidence, payload):
    draft, trace = score_batch(client, prompt, evidence, payload)
    issues = review_draft(client, review_prompt, payload, draft)
    review = {"initial_issues": issues}
    if not issues:
        return draft, trace, review

    # Reassess the same scenario and evidence once using the review feedback.
    payload["scenario_review_feedback"] = issues
    payload["can_request_context"] = False
    draft, trace = score_batch(client, prompt, evidence, payload)
    remaining = review_draft(client, review_prompt, payload, draft)
    review["remaining_issues"] = remaining
    if remaining:
        draft = pending_result(payload, "The scenario check could not verify the relevance decision.", remaining)
    return draft, trace, review


def combine(results, payload_heading):
    highest = max((r["level"] for r in results if r["level"] is not None),
                  key=LEVELS.index, default=None)
    needs_review = any(r["review_status"] == "NEEDS_REVIEW" for r in results)
    level = None if needs_review and highest != "DIRECT" else highest
    leaders = [r for r in results if r["level"] == level]
    pending = [r for r in results if r["review_status"] == "NEEDS_REVIEW"]
    reasons = list(dict.fromkeys(r["reason"] for r in leaders + pending))
    citations = list(dict.fromkeys(ref for r in leaders + pending for ref in r["evidence_refs"]))
    functions = {r["provision_function"] for r in results}
    function = ("SCOPE" if norm(payload_heading or "").split(" / ")[-1] == "scope"
                else next(iter(functions)) if len(functions) == 1 else "MIXED")
    return {
        **{key: results[0][key] for key in ("celex", "source_version_id", "target")},
        "provision_function": function, "level": level, "reason": " ".join(reasons[:3]),
        "area": list(dict.fromkeys(a for r in leaders for a in r["area"])) if level in ("DIRECT", "INDIRECT") else [],
        "evidence_refs": citations,
        "review_status": "NEEDS_REVIEW" if needs_review else "COMPLETE",
        "missing_evidence": list(dict.fromkeys(m for r in results for m in r["missing_evidence"])),
        "branch_results": [b for r in results for b in r["branch_results"]],
    }
