"""Build the fixed company profile and system prompt.

The system prompt is the same text for every request of a run: instructions
first, then the complete business catalog. vLLM prefix caching can only reuse
work for requests that start with exactly the same text, so nothing about a
single act or target may be added here.
"""

import json

ANCHOR_FIELDS = ("label", "scope", "roles", "note", "not_covered", "company_facts")
NEGATIVE_FIELDS = ("label", "text", "except")
NONE = "NONE"
NO_MATCH = "NO_MATCH"
# Other spellings of "no value" that the model may start to write. The reply
# format only lets it finish a listed value, so without these a reply that
# starts with "no" can only end as an anchor such as "noise_vibration".
NULL_ALIASES = ("none", "None", "no_match", "not_applicable", "n/a")


def company_profile(catalog, anchor_facts=None):
    """Return the catalog parts the model needs, in the catalog's own order."""
    required = ("roles", "not_roles", "indirect_paths", "negative_anchors", "anchors")
    if not isinstance(catalog, dict) or any(not catalog.get(key) for key in required):
        raise ValueError("The business anchor catalog needs roles, not_roles, indirect_paths, "
                         "negative_anchors and anchors.")
    facts = anchor_facts or {}
    if unknown := sorted(set(facts) - set(catalog["anchors"])):
        raise ValueError(f"Company facts name unknown anchors: {', '.join(unknown)}")
    anchors = {}
    for anchor_id, anchor in catalog["anchors"].items():
        if not isinstance(anchor_id, str) or not isinstance(anchor, dict):
            raise ValueError("The business anchor catalog must map anchor IDs to definitions.")
        merged = {**anchor, **({"company_facts": facts[anchor_id]} if anchor_id in facts else {})}
        anchors[anchor_id] = {field: merged[field] for field in ANCHOR_FIELDS if merged.get(field)}
    negatives = {
        key: {field: value[field] for field in NEGATIVE_FIELDS if value.get(field)}
        for key, value in catalog["negative_anchors"].items()
    }
    return {
        "roles": dict(catalog["roles"]),
        "not_roles": catalog["not_roles"],
        "indirect_paths": dict(catalog["indirect_paths"]),
        "negative_anchors": negatives,
        "business_anchors": anchors,
    }


def system_prompt(instructions, review_instructions, profile):
    """Instructions, then the catalog. Byte-identical for every request of a run."""
    # One catalog entry per line: compact, and stable from run to run.
    catalog = "\n".join(
        f"{section}:\n" + (value if isinstance(value, str) else "\n".join(
            f"- {key}: {json.dumps(item, ensure_ascii=False)}" for key, item in value.items()))
        for section, value in profile.items())
    return (f"{instructions.strip()}\n\n{review_instructions.strip()}\n\n"
            f"COMPANY PROFILE\n{catalog}\n")


def choices(profile):
    """The IDs the model may select in its reply."""
    anchors, negatives = list(profile["business_anchors"]), list(profile["negative_anchors"])
    if clash := sorted(set(anchors) & set(negatives)):
        raise ValueError(f"Business and negative anchors share IDs: {', '.join(clash)}")
    reserved = {NONE, NO_MATCH, *NULL_ALIASES}
    if clash := sorted(reserved & {*anchors, *negatives, *profile["roles"], *profile["indirect_paths"]}):
        raise ValueError(f"Catalog IDs use reserved words: {', '.join(clash)}")
    return {
        "anchors": anchors,
        "negative_anchors": negatives,
        # The roles Max Bögl holds in each business area.
        "anchor_roles": {key: list(profile["business_anchors"][key].get("roles", ())) for key in anchors},
        # One list: the model picks the entry that best describes the subject, positive or negative.
        "topics": [NONE, *anchors, *negatives, NO_MATCH, *NULL_ALIASES],
        "roles": [NONE, *profile["roles"], *NULL_ALIASES],
        "indirect_paths": [NONE, *profile["indirect_paths"], *NULL_ALIASES],
    }
