"""Compare explicit rated thermal input thresholds with a single supplied rating."""

from decimal import Decimal
import operator
import re


NUMBER = r"\d+(?:[.,]\d+)?"
RATING = re.compile(rf"\b({NUMBER})\s*MW\s+rated thermal input\b", re.I)
OPERATORS = {
    "greater than or equal to": operator.ge,
    "less than or equal to": operator.le,
    "greater than": operator.gt,
    "less than": operator.lt,
    "at least": operator.ge,
    "at most": operator.le,
}
CONDITION = re.compile(
    rf"\brated thermal input\s+(?:of\s+)?({'|'.join(OPERATORS)})\s+({NUMBER})\s*MW\b",
    re.I,
)


def numeric_checks(branches, facts):
    rated_facts = [(fact, RATING.findall(fact)) for fact in facts]
    rated_facts = [(fact, readings) for fact, readings in rated_facts if readings]
    if len(rated_facts) != 1:
        return []
    fact, readings = rated_facts[0]
    if re.search(r"\b(?:not|no|unknown|unverified|or|between)\b", fact, re.I):
        return []
    if len(readings) != 1:
        return []
    rating = Decimal(readings[0].replace(",", "."))
    checks = []
    for branch in branches:
        text = " ".join(branch.get("text", "").split())
        conditions = list(CONDITION.finditer(text))
        if len(conditions) != 1:
            continue
        match = conditions[0]
        # An alternative route into the branch makes this comparison insufficient.
        remainder = text[:match.start()] + text[match.end():]
        if re.search(r"\bor\b", remainder, re.I) or re.search(rf"{NUMBER}\s*MW\b", remainder, re.I):
            continue
        comparison, limit = match.groups()
        met = OPERATORS[comparison.lower()](rating, Decimal(limit.replace(",", ".")))
        checks.append({
            "locator": branch["locator"],
            "condition": match.group(),
            "scenario_rating_mw": str(rating),
            "status": "MET" if met else "NOT_MET",
        })
    return checks
