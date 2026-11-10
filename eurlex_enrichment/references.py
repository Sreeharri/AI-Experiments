"""Resolve English explicit citations against version-specific identifiers."""

import re
from collections import defaultdict

NUMBER = r"\d+[a-z]?"
ANNEX_NUMBER = r"(?:[IVXLCDM]+|\d+[a-z]?)"
ARTICLE_NUMBER = NUMBER + r"(?:\([0-9a-z]+\))*"


def number_list(unit):
    return rf"{unit}(?:\s*(?:,\s*(?:and\s+)?|\s+(?:and|or|to)\s+|\s*[–-]\s*){unit})*"


TABLE_RE = re.compile(
    rf"\bTables?\s+(?P<numbers>{number_list(NUMBER)})(?![\w(])"
    rf"(?:\s*(?:of|in)\s+Part\s+(?P<part>{ANNEX_NUMBER})\b)?"
    rf"(?:\s*(?:of|in)\s+Annex\s+(?P<annex>{ANNEX_NUMBER})\b)?", re.I
)
PART_RE = re.compile(
    rf"\bPart\s+(?P<part>{ANNEX_NUMBER})\s+(?:of|in)\s+Annex\s+(?P<annex>{ANNEX_NUMBER})\b", re.I
)
ARTICLE_RE = re.compile(rf"\bArticles?\s+(?P<numbers>{number_list(ARTICLE_NUMBER)})(?![\w(])", re.I)
ANNEX_RE = re.compile(
    rf"\bAnnex(?:es)?\s+(?P<numbers>{number_list(ANNEX_NUMBER)})\b"
    rf"|\b(?:the\s+)?Annex\s+to\s+this\s+(?:Decision|Directive|Regulation)\b", re.I
)
ACT_RE = re.compile(
    r"\b(?P<kind>Directive|Regulation|Decision)\s+(?:\((?:EU|EC|EEC)\)\s*)?"
    r"(?:No\.?\s+)?(?P<a>\d{1,4})/(?P<b>\d{1,4})(?:/(?:EU|EC|EEC))?\b", re.I
)
OF_ACT = re.compile(r"^\s*(?:,\s*)?(?:of|to)\s+(?:the\s+)?", re.I)
THAT_ACT = re.compile(r"^that\s+(Directive|Regulation|Decision)\b", re.I)


def labels(value):
    """Expand simple numeric ranges, and retain an unresolved range otherwise."""
    values = re.split(r"\s*,\s*(?:and\s+)?|\s+(?:and|or)\s+", value, flags=re.I)
    result = []
    for item in values:
        ends = re.split(r"\s+to\s+|\s*[–-]\s*", item, flags=re.I)
        if len(ends) == 1:
            result.append(item.upper())
        elif len(ends) == 2 and all(x.isdigit() for x in ends):
            low, high = map(int, ends)
            result.extend(map(str, range(low, high + 1)) if 0 <= high - low <= 100 else [item.upper()])
        else:
            result.append(item.upper())
    return result


def act_id(match):
    a, b = match["a"], match["b"]
    kind = match["kind"].lower()
    letter = {"directive": "L", "regulation": "R", "decision": "D"}[kind]
    if len(a) == 4 and 1900 <= int(a) <= 2099:
        return f"3{a}{letter}{int(b):04d}"
    if len(b) == 4 and 1900 <= int(b) <= 2099:
        return f"3{b}{letter}{int(a):04d}"
    if kind == "directive" and len(a) == 2 and int(a) >= 58:
        return f"319{a}L{int(b):04d}"
    return None


def qualifier(text, match):
    tail = text[match.end():]
    if re.match(r"^\s+(?:TFEU|TEU)\b", tail):
        treaty = tail.strip().split()[0]
        return treaty, None
    prefix = OF_ACT.match(tail)
    if prefix is None:
        return None, None
    suffix = tail[prefix.end():]
    direct = ACT_RE.match(suffix)
    if direct:
        return act_id(direct) or direct[0], None
    that = THAT_ACT.match(suffix)
    if that:
        mentions = [act for act in ACT_RE.finditer(text[:match.start()])
                    if act["kind"].casefold() == that[1].casefold()]
        targets = {act_id(act) or act[0] for act in mentions}
        if len(targets) == 1:
            return targets.pop(), None
        return None, f"Unresolved antecedent: {that[0]}"
    if re.match(r"^(?:Directive|Regulation|Decision)\b", suffix, re.I):
        return None, "External instrument lacks a complete identifier"
    return None, None


def excerpt(text, match):
    before = list(re.finditer(r"[.;]\s+(?=[A-Z])", text[:match.start()]))
    after = re.search(r"[.;](?:\s|$)", text[match.end():])
    start = before[-1].end() if before else 0
    end = match.end() + after.end() if after else len(text)
    return text[start:end].strip()


class Resolver:
    def __init__(self, celex, elements, tables):
        self.celex = celex
        self.articles = defaultdict(list)
        self.annexes = defaultdict(list)
        self.tables = tables
        for item in elements.values():
            if item["kind"] == "ARTICLE":
                self.articles[str(item["number"]).upper()].append(item)
            elif item["kind"] == "ANNEX":
                self.annexes[str(item["number"]).upper()].append(item)
        self.by_path = defaultdict(list)
        self.parts = defaultdict(list)
        for item in elements.values():
            self.by_path[item["path"]].append(item["id"])
            if item["kind"] == "ANNEX_PART":
                self.parts[item["parent_id"], str(item["number"]).upper()].append(item["id"])

    def article(self, number):
        match = re.fullmatch(rf"({NUMBER})((?:\([0-9A-Z]+\))*)", number, re.I)
        if not match:
            return []
        article, suffix = match.groups()
        roots = self.articles[article]
        if not suffix:
            return [item["id"] for item in roots]
        indexes = re.findall(r"\(([0-9A-Z]+)\)", suffix, re.I)
        result = []
        for root in roots:
            expected = root["path"] + "".join(
                f"/{kind}/{value.lower()}" for kind, value in zip(
                    ("paragraph", "point", "subpoint"), indexes
                )
            )
            if len(indexes) <= 3:
                result.extend(self.by_path[expected])
        return result

    def annex_ids(self, number):
        if number is None:
            ids = [item["id"] for group in self.annexes.values() for item in group]
            return ids
        return [item["id"] for item in self.annexes[number]]

    def links(self, source_id, text, annex_id=None, part=None):
        def link(kind, name, match, targets=(), external=None, error=None):
            external = external if external != self.celex else None
            ids = list(dict.fromkeys(targets))
            status = (
                "EXTERNAL_TEXT_REQUIRED" if external else
                "RESOLVED" if len(ids) == 1 and not error else "UNRESOLVED"
            )
            reason = error
            if status == "UNRESOLVED" and not reason:
                reason = "Multiple matching targets" if ids else "No exact matching target"
            return {
                "source_id": source_id, "kind": kind, "target_locator": name,
                "target_id": ids[0] if status == "RESOLVED" else None,
                "target_instrument": external or self.celex,
                "status": status, "matched_text": match[0],
                "source_excerpt": excerpt(text, match), "candidate_ids": ids, "issue": reason,
            }

        for match in TABLE_RE.finditer(text):
            annex_number = match["annex"].upper() if match["annex"] else None
            local_annex = self.annex_ids(annex_number) if annex_number else [annex_id]
            named_part = match["part"].upper() if match["part"] else (part if not annex_number else None)
            external, error = qualifier(text, match)
            for number in labels(match["numbers"]):
                candidates = [table["id"] for table in self.tables
                              if table["annex_id"] in local_annex
                              and table["published_number"] == number
                              and (named_part is None or table["part"] == named_part)]
                target = ", ".join(filter(None, [
                    f"Annex {annex_number}" if annex_number else None,
                    f"Part {named_part}" if named_part else None, f"Table {number}",
                ]))
                yield link("CITES_TABLE", target, match, candidates, external, error)

        for match in PART_RE.finditer(text):
            annex_number, part_number = match["annex"].upper(), match["part"].upper()
            candidates = [item for annex_id in self.annex_ids(annex_number)
                          for item in self.parts[annex_id, part_number]]
            external, error = qualifier(text, match)
            yield link("CITES_ANNEX_PART", f"Annex {annex_number}, Part {part_number}",
                       match, candidates, external, error)

        for match in ANNEX_RE.finditer(text):
            external, error = qualifier(text, match)
            numbers = labels(match["numbers"]) if match["numbers"] else [None]
            for number in numbers:
                candidates = self.annex_ids(number)
                if not external and not error and candidates == [source_id]:
                    continue
                yield link("CITES_ANNEX", f"Annex {number}" if number else "the Annex",
                           match, candidates, external, error)

        for match in ARTICLE_RE.finditer(text):
            external, error = qualifier(text, match)
            for number in labels(match["numbers"]):
                candidates = self.article(number)
                if not external and not error and candidates == [source_id]:
                    continue
                yield link("CITES_ARTICLE", f"Article {number}", match, candidates, external, error)
