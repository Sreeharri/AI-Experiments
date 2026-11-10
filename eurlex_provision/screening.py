"""Model request, output validation, bounded context retrieval and batch roll-up.

Qwen classifies each branch. The relevance level and review status of a batch
are derived from those branch statuses in code, so a label cannot contradict
the branch findings it is based on.
"""

import json

from .evidence import CONTEXT_CHAR_BUDGET, norm, size
from .thresholds import numeric_checks

LEVELS = ("IGNORE", "INDIRECT", "DIRECT")
FUNCTIONS = ("SCOPE", "DEFINITION", "OBLIGATION", "EXEMPTION", "TECHNICAL_REQUIREMENT",
             "PROCEDURE", "APPLICATION", "OTHER", "MIXED")
COMPANY = "APPLIES_TO_COMPANY"
OTHER_ACTOR = "APPLIES_TO_OTHER_ACTOR"
OUTSIDE = "OUTSIDE_SCENARIO"
CONTEXT_ONLY = "CONTEXT_ONLY"
OPEN = "NEEDS_REVIEW"
BRANCH_STATUSES = (COMPANY, OTHER_ACTOR, OUTSIDE, CONTEXT_ONLY, OPEN)
NEEDS_FACT = (COMPANY, OTHER_ACTOR, OUTSIDE)
REVIEW = ("COMPLETE", "NEEDS_REVIEW")
MAX_CONTEXT_ROUNDS = 2
MAX_CONTEXT_REQUESTS = 3
BRANCH_REASON_CHARS = 240
REASON_CHARS = 500
NOTE_CHARS = 300
MAX_FACT_REFS = 3
MAX_EXTRA_REFS = 2
MAX_AREAS = 4
MAX_REVIEW_ISSUES = 4
MAX_REVIEW_NOTES = 3
# Endpoint check (27.10.2026) settings for judgement calls with thinking on.
REVIEW_THINKING_SAMPLING = {"temperature": 0.6, "top_p": 0.95, "top_k": 20}
REVIEW_THINKING_TOKENS = 8192
REVIEW_PLAIN_TOKENS = 900


class ModelError(RuntimeError):
    """A model or endpoint failure that can be retried by resuming the run."""


class DecisionValidationError(ModelError):
    """Both successful API responses failed the decision format checks."""


class AnswerTruncated(DecisionValidationError):
    """The scoring response reached its output limit."""


def object_schema(fields):
    return {"type": "object", "properties": fields, "required": list(fields),
            "additionalProperties": False}


def enum(values):
    return {"enum": list(values)}


def bounded(items, maximum, minimum=0):
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}


def limited(chars):
    return {"type": "string", "maxLength": chars}


def numbered_facts(facts):
    return {f"Fact {number}": fact for number, fact in enumerate(facts, 1)}


def derive(statuses):
    """Return (level, review_status) for a set of branch statuses."""
    statuses = set(statuses)
    if COMPANY in statuses:
        level = "DIRECT"
    elif OPEN in statuses:
        level = None
    elif OTHER_ACTOR in statuses:
        level = "INDIRECT"
    else:
        level = "IGNORE"
    return level, "NEEDS_REVIEW" if OPEN in statuses else "COMPLETE"


def response_schema(payload):
    document = payload["document"]
    branches = [branch["locator"] for branch in payload["branches"]]
    legal_refs = sorted(locators(payload))
    facts = list(payload["scenario"]["facts"])

    def branch(statuses, min_facts):
        # The reason comes before the status so the model explains before it decides.
        return object_schema({
            "locator": enum(branches),
            "reason": limited(BRANCH_REASON_CHARS),
            "status": enum(statuses),
            "fact_refs": bounded(enum(facts), MAX_FACT_REFS, min_facts),
            "evidence_refs": bounded(enum(legal_refs), MAX_EXTRA_REFS),
        })

    decision = object_schema({
        "celex": enum([document["celex"]]),
        "source_version_id": enum([document["version_id"]]),
        "target": enum([payload["target"]]),
        "branch_results": bounded({"anyOf": [branch(NEEDS_FACT, 1), branch((CONTEXT_ONLY, OPEN), 0)]},
                                  len(branches), len(branches)),
        "reason": limited(REASON_CHARS),
        "provision_function": enum(FUNCTIONS),
        "area": bounded(enum(payload["candidate_anchors"]), MAX_AREAS, 1),
        "verification_notes": bounded(limited(NOTE_CHARS), 3),
    })
    can_request = payload["can_request_context"]
    return object_schema({
        "result": {"anyOf": [decision, {"type": "null"}]} if can_request else decision,
        "context_requests": bounded(object_schema({
            "locator": limited(200), "instrument": limited(80), "why": limited(NOTE_CHARS),
        }), MAX_CONTEXT_REQUESTS if can_request else 0),
    })


def prepare(evidence, unit, target, batch, common):
    context = evidence.initial(unit, batch)
    branches = [{k: v for k, v in branch.items() if k != "source_ids"} for branch in batch]
    scenario = common["scenario"]
    notes = gather_notes(evidence, branches + context["passages"])
    return {
        **common, "scenario": {**scenario, "facts": numbered_facts(scenario["facts"])},
        "document": evidence.document, "target": target, "target_kind": unit["kind"],
        "target_heading": evidence.elements.get(unit["id"], {}).get("heading"),
        "branches": branches, "numeric_checks": numeric_checks(branches, scenario["facts"]),
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


def text(value, limit=None):
    return isinstance(value, str) and bool(value.strip()) and (limit is None or len(value) <= limit)


def strings(value, limit=None):
    return isinstance(value, list) and all(text(item, limit) for item in value)


def choices(value, allowed, maximum, minimum=0):
    return (isinstance(value, list) and minimum <= len(value) <= maximum
            and len(set(value)) == len(value) and all(item in allowed for item in value))


def validate_answer(value, payload):
    """Check a raw reply. A result is accepted even if context requests were also sent."""
    if not isinstance(value, dict) or set(value) != {"result", "context_requests"}:
        raise ValueError("Expected result and context_requests.")
    result, requests = value["result"], value["context_requests"]
    if not isinstance(requests, list) or len(requests) > MAX_CONTEXT_REQUESTS:
        raise ValueError("Invalid context_requests list.")
    if result is None:
        if not requests:
            raise ValueError("Return a result, or request at least one legal passage.")
        for request in requests:
            if (not isinstance(request, dict) or set(request) != {"locator", "instrument", "why"}
                    or not text(request["locator"]) or not text(request["why"])
                    or not isinstance(request["instrument"], str)):
                raise ValueError("Context requests need locator, instrument (empty if unknown), and why.")
        return
    required = {"celex", "source_version_id", "target", "branch_results", "reason",
                "provision_function", "area", "verification_notes"}
    if not isinstance(result, dict) or set(result) != required:
        raise ValueError("Decision fields are missing or unexpected.")
    document = payload["document"]
    if (result["celex"], result["source_version_id"], result["target"]) != (
        document["celex"], document["version_id"], payload["target"]
    ):
        raise ValueError("Decision identity must match the target and pinned source.")
    if not text(result["reason"], REASON_CHARS):
        raise ValueError(f"The overall reason must be nonempty and at most {REASON_CHARS} characters.")
    if result["provision_function"] not in FUNCTIONS:
        raise ValueError("Use a listed provision_function.")
    if not choices(result["area"], payload["candidate_anchors"].keys(), MAX_AREAS, 1):
        raise ValueError(f"area needs one to {MAX_AREAS} distinct anchor IDs from candidate_anchors.")
    if not strings(result["verification_notes"], NOTE_CHARS) or len(result["verification_notes"]) > 3:
        raise ValueError("verification_notes must contain up to three short, nonempty strings.")

    expected = [branch["locator"] for branch in payload["branches"]]
    legal_refs = locators(payload)
    facts = payload["scenario"]["facts"].keys()
    excluded = {check["locator"] for check in payload.get("numeric_checks", [])
                if check["status"] == "NOT_MET"}
    if not isinstance(result["branch_results"], list):
        raise ValueError("branch_results must be a list.")
    seen = set()
    for branch in result["branch_results"]:
        if (not isinstance(branch, dict)
                or set(branch) != {"locator", "reason", "status", "fact_refs", "evidence_refs"}):
            raise ValueError("Each branch result needs locator, reason, status, fact_refs and evidence_refs.")
        locator, status = branch["locator"], branch["status"]
        if locator not in expected:
            raise ValueError("A branch used an unexpected locator. Copy the exact locators from branches.")
        if locator in seen:
            raise ValueError(f"Duplicate branch result for {locator}. Return each locator once.")
        seen.add(locator)
        if status not in BRANCH_STATUSES:
            raise ValueError(f"{locator}: use one of the listed branch statuses.")
        if not text(branch["reason"], BRANCH_REASON_CHARS):
            raise ValueError(f"{locator}: the reason must be nonempty and at most {BRANCH_REASON_CHARS} characters.")
        if not choices(branch["fact_refs"], facts, MAX_FACT_REFS, 1 if status in NEEDS_FACT else 0):
            raise ValueError(f"{locator}: {status} needs one to {MAX_FACT_REFS} scenario fact IDs in fact_refs."
                             if status in NEEDS_FACT else f"{locator}: fact_refs must be listed fact IDs.")
        if not choices(branch["evidence_refs"], legal_refs, MAX_EXTRA_REFS):
            raise ValueError(f"{locator}: evidence_refs may list up to {MAX_EXTRA_REFS} supplied legal "
                             "locators. Cite scenario facts in fact_refs.")
        if locator in excluded and status in (COMPANY, OTHER_ACTOR):
            raise ValueError(f"{locator}: the supplied rating does not meet the threshold. "
                             "Use OUTSIDE_SCENARIO, or NEEDS_REVIEW with the specific gap.")
    if missing := [locator for locator in expected if locator not in seen]:
        raise ValueError(f"Missing branch results for {'; '.join(missing)}.")


def assemble(base, branches):
    """Build the stored decision from branch results; level and review status are derived."""
    level, review_status = derive(branch["status"] for branch in branches)
    support = {"DIRECT": COMPANY, "INDIRECT": OTHER_ACTOR}.get(level)
    cited = [branch for branch in branches if branch["status"] in {support, OPEN}] or branches
    return {
        "celex": base["celex"], "source_version_id": base["source_version_id"],
        "target": base["target"], "provision_function": base["provision_function"],
        "level": level, "reason": base["reason"],
        "area": list(base["area"]) if level in ("DIRECT", "INDIRECT") else [],
        "evidence_refs": list(dict.fromkeys(ref for branch in cited for ref in branch["evidence_refs"])),
        "review_status": review_status,
        "missing_evidence": base.get("missing_evidence") or [
            f"{branch['locator']}: {branch['reason']}" for branch in branches if branch["status"] == OPEN
        ],
        "verification_notes": list(base["verification_notes"]),
        "branch_results": branches,
    }


def finalize(result):
    branches = [{
        "locator": branch["locator"], "status": branch["status"], "reason": branch["reason"],
        "fact_refs": list(branch["fact_refs"]),
        "evidence_refs": list(dict.fromkeys([branch["locator"], *branch["evidence_refs"]])),
    } for branch in result["branch_results"]]
    return assemble(result, branches)


def model_input(payload):
    return {key: value for key, value in payload.items() if key != "_attempts"}


def record_attempt(payload, response, phase, budget):
    reasoning = response.reasoning or ""
    payload.setdefault("_attempts", []).append({
        "phase": phase,
        "branches": [branch["locator"] for branch in payload["branches"]],
        "max_tokens": budget,
        "finish_reason": response.finish_reason,
        "usage": response.usage,
        "raw_reply": response.content,
        "reasoning_characters": len(reasoning),
        **({"reasoning": reasoning} if reasoning else {}),
    })


def request(client, prompt, payload):
    schema = response_schema(payload)
    budget = 800 + 300 * len(payload["branches"])
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
        {"locator": b["locator"], "status": OPEN, "reason": reason,
         "fact_refs": [], "evidence_refs": [b["locator"]]}
        for b in payload["branches"]
    ]
    return assemble({
        "celex": document["celex"], "source_version_id": document["version_id"],
        "target": payload["target"], "provision_function": "OTHER", "reason": reason,
        "area": [], "missing_evidence": list(issues), "verification_notes": [],
    }, branches)


def score_batch(client, prompt, evidence, payload):
    for round_index in range(MAX_CONTEXT_ROUNDS + 1):
        answer = request(client, prompt, payload)
        if answer["result"] is not None:
            return finalize(answer["result"]), payload["retrieval_log"]
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


def review_basis(payload):
    return [*payload["scenario"]["facts"], *sorted(locators(payload))]


def review_schema(payload):
    issue = object_schema({
        "locator": enum(branch["locator"] for branch in payload["branches"]),
        "problem": limited(NOTE_CHARS),
        "basis": enum(review_basis(payload)),
        "proposed_status": enum(BRANCH_STATUSES),
    })
    return object_schema({
        "issues": bounded(issue, MAX_REVIEW_ISSUES),
        "notes": bounded(limited(NOTE_CHARS), MAX_REVIEW_NOTES),
    })


def review_draft(client, prompt, payload, draft, thinking=True):
    review_input = {
        key: payload[key] for key in (
            "assessment_date", "scenario", "document", "target", "target_kind", "target_heading",
            "branches", "numeric_checks", "context", "footnotes", "retrieved_context",
        ) if key in payload
    }
    review_input["selected_anchors"] = {area: payload["candidate_anchors"][area] for area in draft["area"]}
    review_input["draft"] = draft
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(review_input, ensure_ascii=False)},
    ]
    if thinking:
        settings = {"thinking": True, "sampling": REVIEW_THINKING_SAMPLING, "max_tokens": REVIEW_THINKING_TOKENS}
    else:
        settings = {"thinking": False, "sampling": {"temperature": 0}, "max_tokens": REVIEW_PLAIN_TOKENS}
    response = client.chat(messages, stream=False, seed=42, schema=review_schema(payload),
                           schema_name="provision_scenario_review", **settings)
    record_attempt(payload, response, "review", settings["max_tokens"])
    value, error = response.parse_json()
    if not response.ok:
        raise ModelError(f"Qwen scenario check failed ({response.error_kind}): "
                         f"{client.clean(response.error or error or '')}")
    if response.finish_reason == "length":
        raise DecisionValidationError("Invalid Qwen scenario check: the reply reached the output limit.")
    if error or not isinstance(value, dict) or set(value) != {"issues", "notes"}:
        raise DecisionValidationError(f"Invalid Qwen scenario check: {error or 'expected issues and notes'}")
    branches = {branch["locator"] for branch in payload["branches"]}
    basis = set(review_basis(payload))
    issues, notes = value["issues"], value["notes"]
    if (not isinstance(issues, list) or len(issues) > MAX_REVIEW_ISSUES
            or not strings(notes, NOTE_CHARS) or len(notes) > MAX_REVIEW_NOTES):
        raise DecisionValidationError("Invalid Qwen scenario check: too many or empty issues or notes.")
    for issue in issues:
        if (not isinstance(issue, dict) or set(issue) != {"locator", "problem", "basis", "proposed_status"}
                or issue["locator"] not in branches or issue["basis"] not in basis
                or issue["proposed_status"] not in BRANCH_STATUSES
                or not text(issue["problem"], NOTE_CHARS)):
            raise DecisionValidationError("Invalid Qwen scenario check: an issue needs a branch locator, "
                                          "problem, listed basis and proposed status.")
    return {"issues": issues, "notes": notes}


def blocking_issues(draft, issues):
    """Return the issues that would change the derived level or review status."""
    current = {branch["locator"]: branch["status"] for branch in draft["branch_results"]}
    outcome = (draft["level"], draft["review_status"])
    disputes = [issue for issue in issues if issue["proposed_status"] != current[issue["locator"]]]

    def outcome_with(selected):
        proposed = dict(current)
        proposed.update((issue["locator"], issue["proposed_status"]) for issue in selected)
        return derive(proposed.values())

    if not disputes or outcome_with(disputes) == outcome:
        return []
    # Prefer the disputes that change the result on their own; otherwise they only matter together.
    return [issue for issue in disputes if outcome_with([issue]) != outcome] or disputes


def hold_disputed(draft, disputes):
    """Keep undisputed branches and mark disputed ones for human review."""
    by_locator = {}
    for issue in disputes:
        by_locator.setdefault(issue["locator"], []).append(issue)
    branches = []
    for branch in draft["branch_results"]:
        found = by_locator.get(branch["locator"])
        if found:
            proposals = "; ".join(f"proposes {i['proposed_status']} based on {i['basis']}: {i['problem']}"
                                  for i in found)
            branch = {**branch, "status": OPEN, "draft_status": branch["status"],
                      "reason": f"Scenario check {proposals}"}
        branches.append(branch)
    base = {key: draft[key] for key in ("celex", "source_version_id", "target", "provision_function",
                                        "reason", "area", "verification_notes")}
    return assemble(base, branches)


def score_verified_batch(client, prompt, review_prompt, evidence, payload, review_thinking=True):
    payload.setdefault("_attempts", [])
    review = {}
    try:
        draft, trace = score_batch(client, prompt, evidence, payload)
        checked = review_draft(client, review_prompt, payload, draft, review_thinking)
        disputes = blocking_issues(draft, checked["issues"])
        review.update(initial_issues=checked["issues"], initial_notes=checked["notes"],
                      initial_blocking=disputes)
        if not disputes:
            return draft, trace, review

        payload["scenario_review_feedback"] = disputes
        payload["previous_draft"] = draft
        payload["can_request_context"] = False
        draft, trace = score_batch(client, prompt, evidence, payload)
        checked = review_draft(client, review_prompt, payload, draft, review_thinking)
        remaining = blocking_issues(draft, checked["issues"])
        review.update(remaining_issues=checked["issues"], remaining_notes=checked["notes"],
                      remaining_blocking=remaining)
        if remaining:
            draft = hold_disputed(draft, remaining)
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
            decision, _, subreview = score_verified_batch(
                client, prompt, review_prompt, evidence, sub, review_thinking)
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


def held_reason(item):
    """Summarise why a saved target is not exported: None, OPEN_BRANCH, SCENARIO_CHECK or INVALID_REPLY."""
    if item.get("review_status") != "NEEDS_REVIEW":
        return None
    found = set()

    def scan(review):
        if review.get("validation_error"):
            found.add("INVALID_REPLY")
        if review.get("remaining_blocking"):
            found.add("SCENARIO_CHECK")
        for sub in review.get("split_after_truncation", []):
            scan(sub)

    for review in item.get("scenario_reviews", []):
        scan(review)
    for reason in ("INVALID_REPLY", "SCENARIO_CHECK"):
        if reason in found:
            return reason
    return "OPEN_BRANCH"
