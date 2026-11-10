"""Validate reviewer-supplied links between scenario facts and exact branches."""

RELATIONSHIPS = {"OWN_ACTIVITY", "SUPPLIER_DEPENDENCY", "OUTSIDE_SCENARIO", "CONTEXT_ONLY"}


def check_bindings(scenario, target, locators):
    bindings = scenario.get("branch_bindings", {})
    if not isinstance(bindings, dict):
        raise ValueError("branch_bindings must map targets to exact branch locators.")
    entries = bindings.get(target, {})
    if not isinstance(entries, dict) or entries.keys() - set(locators):
        raise ValueError(f"{target}: branch bindings must use exact locators in the pinned text.")
    for locator, item in entries.items():
        if (not isinstance(item, dict) or set(item) != {"relationship", "fact_numbers", "basis"}
                or not isinstance(item["relationship"], str) or item["relationship"] not in RELATIONSHIPS
                or not isinstance(item["basis"], str) or not item["basis"].strip()
                or not isinstance(item["fact_numbers"], list)
                or not all(type(i) is int and 1 <= i <= len(scenario["facts"]) for i in item["fact_numbers"])
                or (item["relationship"] != "CONTEXT_ONLY" and not item["fact_numbers"])):
            raise ValueError(f"{target}: invalid scenario binding for {locator}.")


def branch_checks(scenario, target, branches):
    bindings = scenario.get("branch_bindings", {}).get(target, {})
    return [{"locator": branch["locator"], **bindings[branch["locator"]]}
            for branch in branches if branch["locator"] in bindings]


def validate_grounding(result, payload):
    checks = {check["locator"]: check for check in payload.get("branch_checks", [])}
    if not checks:
        return
    relationships, problems = set(), []
    for branch in result["branch_results"]:
        check = checks.get(branch["locator"])
        if check is None:
            continue
        status, relationship = branch["status"], check["relationship"]
        if status == "NEEDS_REVIEW":
            continue
        expected = "OUTSIDE_SCENARIO" if relationship == "OUTSIDE_SCENARIO" else "ASSESSED"
        if status != expected:
            problems.append(f"{branch['locator']}: use {expected}. {check['basis']}")
        if relationship in {"OWN_ACTIVITY", "SUPPLIER_DEPENDENCY"}:
            relationships.add(relationship)
    if result["review_status"] == "COMPLETE":
        if "OWN_ACTIVITY" in relationships and result["level"] != "DIRECT":
            problems.append("An assessed own-activity branch supports DIRECT in this supplied scenario.")
        if len(checks) == len(payload["branches"]) and "OWN_ACTIVITY" not in relationships:
            if "SUPPLIER_DEPENDENCY" in relationships and result["level"] != "INDIRECT":
                problems.append("The assessed obligation belongs to the supplier and the scenario establishes "
                                "a dependency. A complete supplier-only batch uses INDIRECT.")
            if not relationships and result["level"] != "IGNORE":
                problems.append("Out-of-scenario and context-only branches supply no relevance for this batch.")
        support = {"DIRECT": "OWN_ACTIVITY", "INDIRECT": "SUPPLIER_DEPENDENCY"}.get(result["level"])
        if support in relationships and not any(
            checks[locator]["relationship"] == support for locator in result["evidence_refs"] if locator in checks
        ):
            problems.append(f"Cite at least one checked {support} branch in the overall evidence_refs.")
    if problems:
        raise ValueError(" ".join(problems))
