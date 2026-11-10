"""Compare saved provision results with expected labels after a run.

The labels are read only here. They are never added to a Qwen request, and
this file is left out of the run fingerprint, so labels and grading can be
corrected without rerunning Qwen.
"""

from .screening import BRANCH_STATUSES, LEVELS, REVIEW


def load_expected(data, selected):
    """Return {(celex, target): labels} for the selected acts, or {} without a file."""
    if data is None:
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("acts"), dict):
        raise ValueError("Expected results need an 'acts' object keyed by CELEX.")
    expected = {}
    for celex, act in data["acts"].items():
        if celex not in selected:
            continue
        if (not isinstance(act, dict) or set(act) != {"source_version_id", "targets"}
                or not isinstance(act["targets"], dict)):
            raise ValueError(f"Expected results for {celex} need source_version_id and targets.")
        if act["source_version_id"] != selected[celex]:
            raise ValueError(f"Expected results for {celex} were labelled for a different source version.")
        for target, labels in act["targets"].items():
            if (not isinstance(labels, dict) or not {"level", "branches"} <= set(labels)
                    or set(labels) - {"level", "review_status", "branches"}
                    or labels["level"] not in (*LEVELS, None)
                    or labels.get("review_status", "COMPLETE") not in REVIEW
                    or not isinstance(labels["branches"], dict) or not labels["branches"]
                    or any(status not in BRANCH_STATUSES for status in labels["branches"].values())):
                raise ValueError(f"Invalid expected labels for {celex} {target}.")
            expected[(celex, target)] = {
                "level": labels["level"],
                "review_status": labels.get("review_status", "COMPLETE"),
                "branches": dict(labels["branches"]),
            }
    return expected


def check_planned(expected, celex, target, locators):
    labels = expected.get((celex, target))
    if labels and (unknown := sorted(labels["branches"].keys() - set(locators))):
        raise ValueError(f"{celex} {target}: expected labels name branches that are not in the "
                         f"pinned text: {'; '.join(unknown)}")


def grade(expected, items, labels_hash=None):
    """Summarise agreement between saved targets and their expected labels."""
    targets, not_run = [], []
    branch_total = branch_matches = result_matches = 0
    for (celex, target), labels in expected.items():
        item = items.get((celex, target))
        if item is None:
            not_run.append(f"{celex} {target}")
            continue
        actual = {branch["locator"]: branch for branch in item["branch_results"]}
        rows = []
        for locator, wanted in labels["branches"].items():
            branch = actual.get(locator, {})
            rows.append({"locator": locator, "expected": wanted, "actual": branch.get("status"),
                         # A branch held after a scenario-check dispute keeps the scorer's status here.
                         "scorer": branch.get("draft_status", branch.get("status"))})
        matched = sum(row["expected"] == row["actual"] for row in rows)
        result_match = (item["level"], item["review_status"]) == (labels["level"], labels["review_status"])
        branch_total += len(rows)
        branch_matches += matched
        result_matches += result_match
        targets.append({
            "celex": celex, "target": target, "result_matches": result_match,
            "expected": {"level": labels["level"], "review_status": labels["review_status"]},
            "actual": {"level": item["level"], "review_status": item["review_status"]},
            "branch_matches": f"{matched}/{len(rows)}",
            "branch_mismatches": [row for row in rows if row["expected"] != row["actual"]],
        })
    return {
        "labels_sha256": labels_hash,
        "targets_graded": len(targets), "result_matches": result_matches,
        "branches_labelled": branch_total, "branch_matches": branch_matches,
        "labelled_targets_not_run": not_run, "targets": targets,
    }
