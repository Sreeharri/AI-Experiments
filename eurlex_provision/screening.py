"""Model request, output validation, bounded context retrieval and batch roll-up."""

import json

from .evidence import CONTEXT_CHAR_BUDGET, norm, size
from .thresholds import numeric_checks

LEVELS = ("IGNORE", "INDIRECT", "DIRECT")
FUNCTIONS = ("SCOPE", "DEFINITION", "OBLIGATION", "EXEMPTION", "TECHNICAL_REQUIREMENT",
             "PROCEDURE", "APPLICATION", "OTHER", "MIXED")
BRANCH_STATUSES = ("ASSESSED", "OUTSIDE_SCENARIO", "NEEDS_REVIEW")
REVIEW = ("COMPLETE", "NEEDS_REVIEW")
MAX_CONTEXT_ROUNDS = 2
MAX_CONTEXT_REQUESTS = 3


class ModelError(RuntimeError):
    """A model or endpoint failure that can be retried by resuming the run."""


class DecisionValidationError(ModelError):
    """Both successful API responses failed the decision format checks."""


class AnswerTruncated(DecisionValidationError):
    """The scoring response reached its output limit."""


def object_schema(fields):
    return {"type": "object", "properties": fields, "required": list(fields),
            "additionalProperties": False}


def response_schema(anchors, branches):
    string = {"type": "string"}
    def enum(values):
        return {"enum": list(values)}

    def bounded(items, maximum, minimum=0):
        return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}

    note = {"type": "string", "maxLength": 300}
    decision = {
        "celex": string, "source_version_id": string, "target": string,
        "provision_function": enum(FUNCTIONS),
        "level": {},
        "reason": {"type": "string", "maxLength": 500},
        "area": {},
        "evidence_refs": bounded(string, 8, 1),
        "review_status": {},
        "missing_evidence": {},
        "verification_notes": bounded(note, 3),
        "branch_results": bounded(object_schema({
            "locator": enum(branches), "status": enum(BRANCH_STATUSES),
            "reason": {"type": "string", "maxLength": 240},
            "evidence_refs": bounded(string, 3, 1),
        }), len(branches), len(branches)),
    }
    options = [
        object_schema({
            **decision,
            "level": enum(levels),
            "review_status": enum([status]),
            "area": bounded(enum(anchors), 4 if has_areas else 0, int(has_areas)),
            "missing_evidence": bounded(note, 3 if status == "NEEDS_REVIEW" else 0,
                                        int(status == "NEEDS_REVIEW")),
        })
        for levels, status, has_areas in (
            (("DIRECT", "INDIRECT"), "COMPLETE", True),
            (("IGNORE",), "COMPLETE", False),
            (("DIRECT",), "NEEDS_REVIEW", True),
            ((None,), "NEEDS_REVIEW", False),
        )
    ]
    return object_schema({
        "result": {"anyOf": [*options, {"type": "null"}]},
        "context_requests": bounded(object_schema({
            "locator": string, "instrument": string, "why": note,
        }), MAX_CONTEXT_REQUESTS),
    })


def prepare(evidence, unit, target, batch, common):
    context = evidence.initial(unit, batch)
    branches = [{k: v for k, v in branch.items() if k != "source_ids"} for branch in batch]
    notes = gather_notes(evidence, branches + context["passages"])
    return {
        **common, "document": evidence.document, "target": target, "target_kind": unit["kind"],
        "target_heading": evidence.elements.get(unit["id"], {}).get("heading"),
        "branches": branches, "numeric_checks": numeric_checks(branches, common["scenario"]["facts"]),
        "context": context, "footnotes": notes["available"],
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
                "area", "evidence_refs", "review_status", "missing_evidence", "verification_notes", "branch_results"}
    if not isinstance(result, dict) or set(result) != required:
        raise ValueError("Decision fields are missing or unexpected.")
    document = payload["document"]
    if (result["celex"], result["source_version_id"], result["target"]) != (
        document["celex"], document["version_id"], payload["target"]
    ):
        raise ValueError("Decision identity must match the target and pinned source.")
    if (result["provision_function"] not in FUNCTIONS or result["level"] not in (*LEVELS, None)
            or result["review_status"] not in REVIEW or not text(result["reason"])
            or len(result["reason"]) > 500):
        raise ValueError("Invalid function, level, review status or reason.")
    areas = result["area"]
    if (not strings(areas) or not set(areas) <= payload["candidate_anchors"].keys()
            or len(set(areas)) != len(areas) or len(areas) > 4
            or bool(areas) != (result["level"] in ("DIRECT", "INDIRECT"))):
        raise ValueError("Use supported anchor IDs and include areas only for DIRECT or INDIRECT.")
    missing = result["missing_evidence"]
    pending = result["review_status"] == "NEEDS_REVIEW"
    if not strings(missing) or len(missing) > 3 or any(len(item) > 300 for item in missing):
        raise ValueError("missing_evidence must contain up to three short, nonempty strings.")
    if not strings(notes := result["verification_notes"]) or len(notes) > 3 or any(len(item) > 300 for item in notes):
        raise ValueError("verification_notes must contain up to three short, nonempty strings.")
    if pending and result["level"] not in (None, "DIRECT"):
        raise ValueError("NEEDS_REVIEW permits only null or an established DIRECT. "
                         "Use null with empty area for an unresolved INDIRECT or IGNORE.")
    if not pending and result["level"] is None:
        raise ValueError("A null level requires review_status NEEDS_REVIEW.")
    if pending and not missing:
        raise ValueError("NEEDS_REVIEW requires at least one gap that could change the level.")
    if not pending and missing:
        raise ValueError("COMPLETE requires empty missing_evidence. Put follow-up checks "
                         "that do not change relevance in verification_notes.")

    expected = {branch["locator"] for branch in payload["branches"]}
    available = locators(payload)
    citations = result["evidence_refs"]
    if (not strings(citations) or len(citations) > 8 or not set(citations) <= available
            or not set(citations) & expected):
        raise ValueError("Decision must cite an exact supplied target branch locator.")
    if not isinstance(result["branch_results"], list):
        raise ValueError("branch_results must be a list.")
    excluded = {check["locator"] for check in payload.get("numeric_checks", [])
                if check["status"] == "NOT_MET"}
    seen, branch_pending = set(), False
    for branch in result["branch_results"]:
        if not isinstance(branch, dict) or set(branch) != {"locator", "status", "reason", "evidence_refs"}:
            raise ValueError("Invalid branch result fields.")
        locator, refs = branch["locator"], branch["evidence_refs"]
        if not text(locator) or locator not in expected:
            raise ValueError("A branch used an unexpected locator. Copy the exact locators from branches.")
        if locator in seen:
            raise ValueError(f"Duplicate branch result for {locator}. Return each locator once.")
        if (branch["status"] not in BRANCH_STATUSES or not text(branch["reason"])
                or len(branch["reason"]) > 240 or not strings(refs) or len(refs) > 3
                or locator not in refs or not set(refs) <= available):
            raise ValueError(f"Branch {locator} must have a valid status, reason and exact evidence citations.")
        if locator in excluded and branch["status"] == "ASSESSED":
            raise ValueError(f"{locator}: the supplied rating does not meet the threshold. "
                             "Use OUTSIDE_SCENARIO or NEEDS_REVIEW with a material gap.")
        branch_pending |= branch["status"] == "NEEDS_REVIEW"
        seen.add(locator)
    if missing_branches := expected - seen:
        raise ValueError(f"Missing branch results for {'; '.join(sorted(missing_branches))}.")
    if branch_pending and not pending:
        raise ValueError("A branch is NEEDS_REVIEW. Set overall review_status to NEEDS_REVIEW "
                         "and list the material gaps in missing_evidence.")
    if all(b["status"] == "OUTSIDE_SCENARIO" for b in result["branch_results"]) and result["level"] not in ("IGNORE", None):
        raise ValueError("Outside-scenario branches cannot establish DIRECT or INDIRECT.")


def model_input(payload):
    return {key: value for key, value in payload.items() if key != "_attempts"}


def record_attempt(payload, response, phase, budget):
    payload.setdefault("_attempts", []).append({
        "phase": phase,
        "branches": [branch["locator"] for branch in payload["branches"]],
        "max_tokens": budget,
        "finish_reason": response.finish_reason,
        "usage": response.usage,
        "raw_reply": response.content,
        "reasoning_characters": len(response.reasoning),
    })


def request(client, prompt, payload):
    branch_labels = [branch["locator"] for branch in payload["branches"]]
    schema = response_schema(payload["candidate_anchors"], branch_labels)
    budget = 800 + 300 * len(branch_labels)
    messages = [{"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(model_input(payload), ensure_ascii=False)}]
    for attempt in range(2):
        response = client.chat(messages, thinking=False, stream=False, seed=42,
                               sampling={"temperature": 0}, schema=schema,
                               schema_name="provision_relevance", max_tokens=budget)
        record_attempt(payload, response, "scoring", budget)
        value, error = response.parse_json()
        if not response.ok:
            raise ModelError(f"Qwen request failed ({response.error_kind}): {client.clean(response.error or error or '')}")
        if response.finish_reason == "length":
            raise AnswerTruncated("Qwen scoring reply reached the output limit.")
        try:
            if error:
                raise ValueError(error)
            validate_answer(value, payload)
            return value
        except ValueError as exc:
            if attempt:
                raise DecisionValidationError(f"Qwen response failed validation twice: {exc}") from exc
            messages[1]["content"] = json.dumps({
                **model_input(payload), "response_validation_feedback": str(exc),
                "rejected_answer": value if value is not None else response.content[-2400:],
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
        "review_status": "NEEDS_REVIEW", "missing_evidence": issues,
        "verification_notes": [], "branch_results": branches,
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
        "issues": {"type": "array", "items": {"type": "string", "maxLength": 300}, "maxItems": 4},
    })
    review_input = {k: v for k, v in model_input(payload).items()
                    if k not in {"scenario_review_feedback", "previous_draft"}}
    review_input["draft"] = draft
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(review_input, ensure_ascii=False)},
    ]
    response = client.chat(
        messages, thinking=False, stream=False, seed=42, sampling={"temperature": 0},
        schema=schema, schema_name="provision_scenario_review", max_tokens=600,
    )
    record_attempt(payload, response, "review", 600)
    value, error = response.parse_json()
    if not response.ok:
        raise ModelError(f"Qwen scenario check failed ({response.error_kind}): "
                         f"{client.clean(response.error or error or '')}")
    if error or not isinstance(value, dict) or set(value) != {"issues"}:
        raise DecisionValidationError(f"Invalid Qwen scenario check: {error or 'expected an issues array'}")
    issues = value["issues"]
    if not strings(issues) or len(issues) > 4:
        raise DecisionValidationError("Invalid Qwen scenario check: expected up to four nonempty issues.")
    return issues


def score_verified_batch(client, prompt, review_prompt, evidence, payload):
    payload.setdefault("_attempts", [])
    review = {}
    try:
        draft, trace = score_batch(client, prompt, evidence, payload)
        issues = review_draft(client, review_prompt, payload, draft)
        review["initial_issues"] = issues
        if not issues:
            return draft, trace, review

        payload["scenario_review_feedback"] = issues
        payload["previous_draft"] = draft
        payload["can_request_context"] = False
        draft, trace = score_batch(client, prompt, evidence, payload)
        remaining = review_draft(client, review_prompt, payload, draft)
        review["remaining_issues"] = remaining
        if remaining:
            draft = pending_result(payload, "The scenario check could not verify the relevance decision.", remaining)
        return draft, trace, review
    except AnswerTruncated as exc:
        branches = payload["branches"]
        if len(branches) == 1:
            return hold_invalid_decision(payload, review, exc)
        middle = len(branches) // 2
        results, subreviews = [], []
        for subset in (branches[:middle], branches[middle:]):
            keys = {branch["locator"] for branch in subset}
            # Retain retrieved context and its audit, but assess only this subset.
            sub = {k: v for k, v in payload.items()
                   if k not in {"scenario_review_feedback", "previous_draft"}}
            sub.update(branches=subset, can_request_context=True,
                       numeric_checks=[c for c in payload.get("numeric_checks", []) if c["locator"] in keys])
            decision, _, subreview = score_verified_batch(client, prompt, review_prompt, evidence, sub)
            results.append(decision)
            subreviews.append(subreview)
        review["split_after_truncation"] = subreviews
        notes = gather_notes(evidence, branches + payload["context"]["passages"] + payload["retrieved_context"])
        payload.update(footnotes=notes["available"], missing_footnotes=notes["missing"])
        return combine(results, payload.get("target_heading")), payload["retrieval_log"], review
    except DecisionValidationError as exc:
        return hold_invalid_decision(payload, review, exc)


def hold_invalid_decision(payload, review, error):
    reason = "No valid assessment was returned for all branches."
    review["validation_error"] = str(error)
    decision = pending_result(payload, reason, [str(error)])
    return decision, payload["retrieval_log"], review


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
        "verification_notes": list(dict.fromkeys(m for r in results for m in r["verification_notes"])),
        "branch_results": [b for r in results for b in r["branch_results"]],
    }
