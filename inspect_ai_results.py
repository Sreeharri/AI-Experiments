"""Check all AI results for known kinds of mistakes, without Qwen and without changing anything.

Reads the saved results of every AI step and lists, per kind of mistake, how often it occurs and
examples to check by hand:

    document level   relevant acts with no relevant provision, acts from areas far from the business
    provision level  rules for authorities marked DIRECT, formal clauses and amending articles kept
    classification   no domain, no department, the checks the classification wrote itself
    summaries v1/v2  "may" written as "muss", authority duties without the authority, amending articles
                     summarised as rules, annex summaries that start with "Die Verordnung", English text,
                     number marks, missing summaries
    act/department   summaries that failed

Every check only flags; it changes nothing. The run folders are read, never written.

    python inspect_ai_results.py

Output: runs/inspection/<time>/report.md (counts and examples), issues.csv (one row per flagged
item, for Excel) and summary.json.
"""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from classify_eurlex_provisions import DEFAULT_RUNS, DEFAULT_UNSURE, batch_folders, target_text  # noqa: E402

DOCUMENT_RUN = ROOT / "runs/eurlex_full_v10"
CLASSIFICATION = ROOT / "runs/eurlex_classification_v5"
SUMMARIES_V1 = ROOT / "runs/eurlex_summaries_v1"
SUMMARIES_V2 = ROOT / "runs/eurlex_summaries_v2_changes"
NEW_ARTICLES = ROOT / "runs/eurlex_new_articles"
OUTPUT = ROOT / "runs/inspection"
KEPT = ("DIRECT", "INDIRECT")
EXAMPLES = 12

# ------------------------------------------------------------------ the checks and what they mean

CHECKS = {
    "DOC_NO_KEPT_PROVISION": ("Document", "Relevant act, but the provision screening kept no provision (all IGNORE). "
                              "The act decision may be too broad."),
    "DOC_DIRECT_NO_DIRECT_PROVISION": ("Document", "DIRECT act without a single DIRECT provision (only INDIRECT). "
                                       "Information: the act may be INDIRECT."),
    "DOC_FAR_AREA": ("Document", "Relevant act whose title names an area far from the business (feed, plant "
                     "protection, food, medicines, fisheries, ...). To check by hand."),
    "PROV_AUTHORITY_DIRECT": ("Provision", "DIRECT, but the rule binds an authority (Member States, the Commission, "
                              "a competent authority), not a business. Should be INDIRECT at most."),
    "PROV_VIA_MEMBER_STATES_DIRECT": ("Provision", "DIRECT, the rule binds Member States to make businesses do "
                                      "something (\"Member States shall ensure that operators ...\"). Directive "
                                      "duty, reaches the company through national law. Information."),
    "PROV_FORMAL_KEPT": ("Provision", "Kept although it looks like a formal clause (entry into force, addressees, "
                         "repeal, committee procedure, transposition)."),
    "PROV_AMENDING": ("Provision", "Kept article that only changes another act (\"... is amended as follows\"). "
                      "Its relevance and summary should follow the act it changes."),
    "CLASS_NO_DOMAIN": ("Classification", "No fitting domain."),
    "CLASS_NO_DEPARTMENT": ("Classification", "No department for the domain."),
    "CLASS_AUTHORITY_ROUTED": ("Classification", "Authority rule (PROV_AUTHORITY_DIRECT) sent to departments as a "
                               "DIRECT provision."),
    "CLASS_CHECK": ("Classification", "Flag the classification wrote itself (see the detail column)."),
    "SUM_MAY_AS_MUST": ("Summary", "A point says \"muss\" / \"verpflichtet\" where the quoted text says \"may\" "
                        "(a permission written as a duty)."),
    "SUM_AUTHORITY_UNNAMED": ("Summary", "A duty point taken from \"Member States / the Commission / the authority "
                              "shall ...\" that does not name the authority (reads like a company duty)."),
    "SUM_AMENDING_AS_RULE": ("Summary", "Amending article summarised as if it were a rule of its own (no word "
                             "about the change of the other act)."),
    "SUM_ANNEX_AS_ACT": ("Summary", "Annex summary that starts with \"Die Verordnung / Die Richtlinie ...\" "
                         "instead of the annex."),
    "SUM_ENGLISH": ("Summary", "Headline or overview in English."),
    "SUM_NUMBER_MARK": ("Summary", "Number, date or unit marked by the number check (not found in the text)."),
    "SUM_MISSING": ("Summary", "No summary (failed)."),
    "ACT_FAILED": ("Act / department", "Act summary failed."),
    "DEPARTMENT_FAILED": ("Act / department", "Department summary failed."),
    "V2_SENTENCE_NUMBER_MARK": ("Summary v2", "\"Was hat sich geändert\" sentence with a number mark."),
}

# ------------------------------------------------------------------ text patterns

AUTHORITY = re.compile(
    r"\b(?:Member States?|the Commission|Commission shall|competent authorit(?:y|ies)|national (?:regulatory |"
    r"competent )?authorit(?:y|ies)|regulatory authorit(?:y|ies)|approval authorit(?:y|ies)|market surveillance "
    r"authorit(?:y|ies)|customs authorit(?:y|ies)|supervisory authorit(?:y|ies)|designated authorit(?:y|ies)|"
    r"public authorit(?:y|ies)|the Agency|the Authority|the Council|the Board|the Committee|ESMA|EBA|EIOPA|ACER|"
    r"ENISA|Eurostat|the Office)\b", re.IGNORECASE)
BUSINESS = re.compile(
    r"\b(?:operators?|undertakings?|manufacturers?|employers?|producers?|importers?|distributors?|contractors?|"
    r"economic operators?|installations?|companies|company|enterprises?|suppliers?|users?|owners?|holders?|"
    r"applicants?|traders?|carriers?|providers?|entit(?:y|ies)|persons?|shippers?|hauliers?|consignors?|"
    r"consignees?|verifiers?|taxable persons?|taxpayers?|investors?|issuers?|bidders?|tenderers?|"
    r"intermediar(?:y|ies)|market participants?|transmission system operators?|TSOs?|DSOs?|"
    r"insurance undertakings?|credit institutions?|auditors?|vehicles?)\b", re.IGNORECASE)
SHALL = re.compile(r"\b(?:shall|must)\b")
ENSURE = re.compile(r"\b(?:ensure|require|lay down rules|oblige|provide that)\b", re.IGNORECASE)
SENTENCE_START = re.compile(r".*(?:[.;:]\s|\(\w{1,4}\)\s|^)", re.DOTALL)
MAY = re.compile(r"\bmay\b(?!\s+not\b)")
NOT_MAY = re.compile(r"\bcase may be\b|\bmay be\b[^.]{0,30}\bshall\b", re.IGNORECASE)
DUTY_DE = re.compile(r"\b(?:muss|müssen|ist verpflichtet|sind verpflichtet|hat die Pflicht|haben die Pflicht|"
                     r"verpflichtet)\b", re.IGNORECASE)
PERMISSION_DE = re.compile(r"\b(?:kann|können|darf|dürfen|berechtigt|möglich|dürfte|könnte)\b", re.IGNORECASE)
AUTHORITY_DE = re.compile(r"(?:Mitgliedstaat|Kommission|Behörde|Stelle|Agentur|Rat\b|Ausschuss|Union|Staat|"
                          r"Zollbehörde|Aufsicht|Verwaltung|Regierung|Amt\b|Ämter|Organ|\bACER\b|\bESMA\b|"
                          r"\bEBA\b|\bEIOPA\b|\bENISA\b|\bECHA\b|\bEFSA\b|\bEASA\b|\bERA\b|Eurostat|"
                          r"Netzbetreiber|Regulierungs)", re.IGNORECASE)
AMENDING = re.compile(
    r"\b(?:is|are) (?:hereby )?amended as follows|\b(?:is|are) replaced by the following|\bshall be replaced by\b|"
    r"\bthe following (?:article|articles|paragraph|paragraphs|point|points|subparagraph|annex|annexes|"
    r"sentence|indent) (?:is|are) (?:added|inserted)|\b(?:is|are) (?:hereby )?(?:inserted|deleted)\b", re.IGNORECASE)
AMENDED_DE = re.compile(r"(?:änder|ersetz|eingefügt|gestrichen|neu gefasst|Änderung)", re.IGNORECASE)
FORMAL = re.compile(
    r"shall enter into force|(?:is|are) addressed to the Member States|(?:is|are) hereby repealed|"
    r"shall be assisted by a committee|shall bring into force the laws, regulations and administrative provisions|"
    r"correlation table|shall be binding in its entirety|power to adopt delegated acts|exercise of the delegation|"
    r"delegated act adopted pursuant to", re.IGNORECASE)
ANNEX_AS_ACT = re.compile(r"^(?:Die|Diese) (?:Verordnung|Richtlinie|Durchführungsverordnung|Delegierte Verordnung|"
                          r"Entscheidung)|^(?:Der|Dieser) (?:Beschluss|Durchführungsbeschluss)")
FAR_AREA = re.compile(
    r"feed additive|additive in feed|feedingstuff|plant protection|active substance|pesticide|maximum residue|"
    r"veterinary|medicinal product|novel food|food additive|foodstuff|fishing|fisheries|fishery|aquaculture|"
    r"\bwine\b|spirit drinks|\bmilk\b|dairy|cereals|olive|tobacco|cosmetic|organic production|animal health|"
    r"\bseeds?\b|fruit and vegetables|\bsugar\b|\bbeef\b|poultry|bovine|\bhoney\b|banana|vessels? flying",
    re.IGNORECASE)
EN_WORDS = re.compile(r"\b(?:the|of|and|for|shall|with|which|between|when|on|to|from|by|under)\b")
DE_WORDS = re.compile(r"\b(?:der|die|das|den|dem|des|und|für|von|vom|zur|zum|bei|mit|im|über|nach|aus|ein|eine|"
                      r"einer|eines|ist|sind|wird|werden)\b", re.IGNORECASE)


def english(text):
    """English rather than German: more English function words than German ones."""
    text = text or ""
    return len(EN_WORDS.findall(text)) >= 1 and len(EN_WORDS.findall(text)) > len(DE_WORDS.findall(text))


def shall_sides(text):
    """(authority, business, via Member States): how often "shall/must" follows an authority or a business."""
    authority = business = via = 0
    for match in SHALL.finditer(text or ""):
        before = text[max(0, match.start() - 90):match.start()]
        before = before[SENTENCE_START.match(before).end():]   # only the sentence of this "shall"
        after = text[match.end():match.end() + 200]
        if AUTHORITY.search(before):
            authority += 1
            if ENSURE.search(after[:70]) and BUSINESS.search(after):
                via += 1
        elif BUSINESS.search(before):
            business += 1
    return authority, business, via


def authority_kind(actor, text):
    """'authority' when the provision binds only an authority, 'via' when it binds Member States to make
    businesses act, None otherwise."""
    authority, business, via = shall_sides(text)
    if business:
        return None
    if via:
        return "via"
    if AUTHORITY.search(actor or "") and not BUSINESS.search(actor or ""):
        return "authority"
    if authority and not business:
        return "authority"
    return None


def snippet(text, pattern=None, width=220):
    text = re.sub(r"\s+", " ", text or "").strip()
    if pattern is not None and (match := pattern.search(text)):
        start = max(0, match.start() - width // 3)
        return ("…" if start else "") + text[start:start + width] + ("…" if start + width < len(text) else "")
    return text[:width] + ("…" if len(text) > width else "")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_or_none(path):
    """A saved record, or None when it cannot be read (for example while another run writes it)."""
    try:
        return read_json(path)
    except (OSError, ValueError):
        return None


def pick(items, count=EXAMPLES):
    """Examples spread over different acts, the same each time."""
    chosen, acts = [], set()
    for item in sorted(items, key=lambda i: hashlib.sha1(f"{i['celex']}|{i['provision']}".encode()).hexdigest()):
        if item["celex"] not in acts:
            chosen.append(item)
            acts.add(item["celex"])
        if len(chosen) == count:
            return chosen
    rest = [item for item in items if item not in chosen]
    return chosen + rest[:count - len(chosen)]


# ------------------------------------------------------------------ reading the runs

class Findings:
    def __init__(self):
        self.items = defaultdict(list)
        self.totals = Counter()
        self.notes = []

    def add(self, check, celex, provision="", level="", detail="", evidence="", source=""):
        self.items[check].append({"check": check, "celex": celex, "provision": provision, "level": level,
                                  "detail": detail, "evidence": evidence, "source": source})


def screened(runs, unsure_dirs, notes):
    """Every screened provision with its final level: {(celex, target): {level, file, ...}}. An act screened
    in a later run counts only from that run (as in the classification)."""
    owner, results = {}, {}
    for position, run in enumerate(runs):
        for folder in batch_folders(run) if run.is_dir() else []:
            manifest = folder / "manifest.json"
            for celex in (read_json(manifest) if manifest.is_file() else {}):
                owner[celex] = position
    resolved = {}
    for folder in unsure_dirs:
        if (folder / "results.json").is_file():
            for celex, targets in read_json(folder / "results.json").items():
                resolved.setdefault(celex, {}).update(targets)
    for position, run in enumerate(runs):
        if not run.is_dir():
            notes.append(f"Not found, left out: {run}")
            continue
        for folder in batch_folders(run):
            summary = folder / "audit" / "run_summary.json"
            if not summary.is_file():
                continue
            for item in read_json(summary).get("results", []):
                if owner.get(item["celex"], position) != position:
                    continue
                level = item.get("level")
                if level == "UNSURE":
                    level = (resolved.get(item["celex"], {}).get(item["target"]) or {}).get("level", "UNSURE")
                results[(item["celex"], item["target"])] = {
                    "level": level, "file": folder / item["file"], "kind": item.get("kind", ""),
                    "first_level": item.get("level")}
    return results


def new_article_runs():
    if not NEW_ARTICLES.is_dir():
        return [], [], []
    folders = sorted(path for path in NEW_ARTICLES.iterdir() if (path / "screening").is_dir())
    return ([f / "screening" for f in folders], [f / "unsure" for f in folders],
            [f / "classification" for f in folders])


def classifications(folders):
    merged = {}
    for folder in folders:
        if (folder / "results.json").is_file():
            for celex, targets in read_json(folder / "results.json").items():
                merged.setdefault(celex, {}).update(targets)
    return merged


def document_decisions(run, notes):
    decisions = {}
    for level in ("direct", "indirect"):
        path = run / "relevance_export" / f"{level}.json"
        if path.is_file():
            for celex, decision in read_json(path).items():
                decisions[celex] = decision
        else:
            notes.append(f"Not found, left out: {path}")
    return decisions


# ------------------------------------------------------------------ the checks

def title_of(celex, files):
    for path in files.get(celex, [])[:1]:
        try:
            return (read_json(path).get("evidence_pack", {}).get("document") or {}).get("title") or ""
        except (OSError, ValueError):
            return ""
    return ""


def check_documents(found, decisions, provisions, titles):
    by_act, files = defaultdict(Counter), defaultdict(list)
    for (celex, _), item in provisions.items():
        by_act[celex][item["level"]] += 1
        files[celex].append(item["file"])
    found.totals["relevant acts (document level)"] = len(decisions)
    for celex, decision in sorted(decisions.items()):
        decision = decision if isinstance(decision, dict) else {}
        level = decision.get("level") or ""
        title = str(decision.get("title") or titles.get(celex) or title_of(celex, files) or "")
        counts = by_act.get(celex)
        if counts and not any(counts[k] for k in KEPT):
            found.add("DOC_NO_KEPT_PROVISION", celex, "", level, f"{sum(counts.values())} provisions, all IGNORE",
                      snippet(title, width=160))
        if level == "DIRECT" and counts and counts["INDIRECT"] and not counts["DIRECT"]:
            found.add("DOC_DIRECT_NO_DIRECT_PROVISION", celex, "", level, f"INDIRECT {counts['INDIRECT']}",
                      snippet(title, width=160))
        if FAR_AREA.search(title):
            found.add("DOC_FAR_AREA", celex, "", level, FAR_AREA.search(title).group(0), snippet(title, width=200))


def check_provisions(found, provisions, classified):
    """Reads the text pack of every kept provision. Returns {(celex, target): flags} and the titles."""
    flags, titles = defaultdict(set), {}
    kept = {key: item for key, item in provisions.items() if item["level"] in KEPT}
    found.totals["kept provisions (DIRECT/INDIRECT)"] = len(kept)
    found.totals["DIRECT provisions"] = sum(item["level"] == "DIRECT" for item in kept.values())
    for (celex, target), item in sorted(kept.items()):
        try:
            pack = read_json(item["file"])
        except (OSError, ValueError):
            continue
        evidence = pack.get("evidence_pack", {})
        provision = evidence.get("provision", {})
        text = " ".join(target_text(provision))
        details = pack.get("details") or {}
        if not titles.get(celex):
            titles[celex] = (evidence.get("document") or {}).get("title") or ""
        actor = details.get("actor") or ""
        if item["level"] == "DIRECT":
            kind = authority_kind(actor, text)
            if kind == "authority":
                flags[(celex, target)].add("authority")
                found.add("PROV_AUTHORITY_DIRECT", celex, target, "DIRECT", f"actor: {actor}",
                          snippet(text, AUTHORITY), str(item["file"]))
                result = (classified.get(celex) or {}).get(target)
                if result and result.get("departments"):
                    found.add("CLASS_AUTHORITY_ROUTED", celex, target, "DIRECT",
                              ", ".join(result["departments"][:4]), snippet(text, AUTHORITY))
            elif kind == "via":
                found.add("PROV_VIA_MEMBER_STATES_DIRECT", celex, target, "DIRECT", f"actor: {actor}",
                          snippet(text, AUTHORITY), str(item["file"]))
        if len(text) < 1500 and FORMAL.search(text):
            found.add("PROV_FORMAL_KEPT", celex, target, item["level"], FORMAL.search(text).group(0),
                      snippet(text, FORMAL), str(item["file"]))
        if AMENDING.search(text[:400]):
            flags[(celex, target)].add("amending")
            found.add("PROV_AMENDING", celex, target, item["level"], AMENDING.search(text[:400]).group(0),
                      snippet(text, AMENDING), str(item["file"]))
    return flags, titles


def check_classification(found, classified):
    total = 0
    for celex, targets in sorted(classified.items()):
        for target, result in sorted(targets.items()):
            total += 1
            if not isinstance(result, dict):
                continue
            if result.get("primary_domain") in (None, "", "no_fitting_domain"):
                found.add("CLASS_NO_DOMAIN", celex, target, result.get("level") or "", result.get("subject") or "",
                          snippet(result.get("primary_reason")))
            departments = [d for d in result.get("departments") or [] if d and "no_department" not in d.lower()]
            if not departments:
                found.add("CLASS_NO_DEPARTMENT", celex, target, result.get("level") or "",
                          result.get("primary_domain") or "", snippet(result.get("subject")))
            for flag in result.get("check") or []:
                found.add("CLASS_CHECK", celex, target, result.get("level") or "", str(flag),
                          f"{result.get('primary_domain')}: {snippet(result.get('primary_reason'), width=160)}")
    found.totals["classified provisions"] = total


def check_summary(found, prefix, celex, target, level, summary, flags, text_start=""):
    """The summary checks for one provision summary (v1 or v2)."""
    headline, overview = summary.get("headline") or "", summary.get("overview") or ""
    for bullet in summary.get("bullets") or []:
        point = bullet.get("text") or ""
        quotes = " ".join(quote for quote in bullet.get("quotes") or [] if isinstance(quote, str))
        if (DUTY_DE.search(point) and not PERMISSION_DE.search(point) and MAY.search(quotes)
                and not SHALL.search(quotes) and not NOT_MAY.search(quotes)):
            found.add(f"{prefix}SUM_MAY_AS_MUST", celex, target, level, snippet(point, width=200),
                      snippet(quotes, MAY))
        if bullet.get("kind") in ("DUTY", "WHO", "DEADLINE") and DUTY_DE.search(point) and quotes:
            authority, business, via = shall_sides(quotes)
            if authority and not business and via < authority and not AUTHORITY_DE.search(point):
                found.add(f"{prefix}SUM_AUTHORITY_UNNAMED", celex, target, level, snippet(point, width=200),
                          snippet(quotes, AUTHORITY))
    if "amending" in flags and not AMENDED_DE.search(f"{headline} {overview}"):
        found.add(f"{prefix}SUM_AMENDING_AS_RULE", celex, target, level, snippet(headline, width=160),
                  snippet(text_start or overview))
    if target.startswith("Annex") and ANNEX_AS_ACT.search(overview.strip()):
        found.add(f"{prefix}SUM_ANNEX_AS_ACT", celex, target, level, snippet(overview, width=160))
    if english(headline) or english(overview):
        found.add(f"{prefix}SUM_ENGLISH", celex, target, level, snippet(headline, width=160))
    marks = [*(summary.get("number_check") or []),
             *(m for b in summary.get("bullets") or [] for m in b.get("number_check") or [])]
    if marks:
        found.add(f"{prefix}SUM_NUMBER_MARK", celex, target, level, "; ".join(map(str, marks))[:200],
                  snippet(headline, width=120))


def check_summaries_v1(found, folder, flags, notes):
    targets = folder / "provisions" / "targets"
    if not targets.is_dir():
        notes.append(f"Not found, left out: {targets}")
        return
    total = 0
    for path in sorted(targets.glob("*/*.json")):
        record = read_or_none(path)
        if record is None:
            notes.append(f"Could not read: {path}")
            continue
        total += 1
        celex, target, level = record.get("celex") or "", record.get("target") or "", record.get("level") or ""
        if not isinstance(record.get("summary"), dict):
            found.add("SUM_MISSING", celex, target, level, str(record.get("failure") or "")[:200])
            continue
        check_summary(found, "", celex, target, level, record["summary"], flags.get((celex, target), set()),
                      record.get("text", ""))
    found.totals["provision summaries v1"] = total
    for kind, sub in (("ACT_FAILED", "acts"), ("DEPARTMENT_FAILED", "departments")):
        count = 0
        for path in sorted((folder / sub).glob("*.json")) if (folder / sub).is_dir() else []:
            record = read_or_none(path) or {"failure": "could not be read"}
            count += 1
            if record.get("failure"):
                found.add(kind, record.get("celex") or path.stem, "", "", str(record["failure"])[:200])
        found.totals[f"{sub} summaries v1"] = count


def check_summaries_v2(found, folder, flags, notes):
    units = folder / "units"
    if not units.is_dir():
        notes.append(f"Not found, left out: {units}")
        return
    total = 0
    for path in sorted(units.glob("*/*.json")):
        record = read_or_none(path)
        if record is None:
            notes.append(f"Could not read: {path}")
            continue
        total += 1
        celex, name = record.get("celex") or "", record.get("name") or ""
        level = ",".join(sorted({p.get("level") or "" for p in record.get("provisions") or []}))
        if record.get("failure"):
            found.add("V2_SUM_MISSING", celex, name, level, str(record["failure"])[:200])
            continue
        if isinstance(record.get("summary"), dict):
            unit_flags = set().union(*[flags.get((celex, p.get("locator", "")), set())
                                       for p in record.get("provisions", [])])
            check_summary(found, "V2_", celex, name, level, record["summary"], unit_flags)
        marks = (record.get("what_changed") or {}).get("number_check") or []
        if marks:
            found.add("V2_SENTENCE_NUMBER_MARK", celex, name, level, "; ".join(marks)[:200],
                      snippet((record.get("what_changed") or {}).get("sentence", ""), width=200))
    found.totals["changed articles/annexes v2"] = total


# ------------------------------------------------------------------ output

def meaning(check):
    base = check[3:] if check.startswith("V2_") and check[3:] in CHECKS else check
    area, text = CHECKS.get(base, ("", ""))
    return ("Summary v2" if check.startswith("V2_") and base != check else area), text


def write_report(out, found):
    out.mkdir(parents=True, exist_ok=True)
    order = [c for c in CHECKS if c in found.items] + sorted(
        c for c in found.items if c not in CHECKS)
    lines = ["# Inspection of the AI results", "", f"Made {datetime.now():%Y-%m-%d %H:%M}. Nothing was changed.", "",
             "## Totals", ""]
    lines += [f"- {name}: {value:,}" for name, value in found.totals.items()]
    if found.notes:
        lines += ["", "## Left out", ""] + [f"- {note}" for note in found.notes]
    lines += ["", "## Counts", "", "| Area | Check | Count | What it means |", "|---|---|---|---|"]
    for check in order:
        area, text = meaning(check)
        detail = ""
        if check == "CLASS_CHECK":
            detail = " (" + ", ".join(f"{k} {v:,}" for k, v in
                                      Counter(i["detail"] for i in found.items[check]).most_common(8)) + ")"
        lines.append(f"| {area} | {check} | {len(found.items[check]):,}{detail} | {text} |")
    for check in order:
        items = found.items[check]
        lines += ["", f"## {check} ({len(items):,})", "", meaning(check)[1], ""]
        for item in pick(items):
            lines.append(f"- **{item['celex']} {item['provision']}** {item['level']} | {item['detail']}"
                         + (f"\n  > {item['evidence']}" if item["evidence"] else ""))
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with (out / "issues.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, ["check", "celex", "provision", "level", "detail", "evidence", "source"],
                                delimiter=";")
        writer.writeheader()
        for check in order:
            writer.writerows(found.items[check])
    summary = {"totals": dict(found.totals), "counts": {c: len(found.items[c]) for c in order},
               "left_out": found.notes}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    found = Findings()
    screening, unsure, classification = new_article_runs()
    print("Reading the provision screening ...", flush=True)
    provisions = screened([*DEFAULT_RUNS], [DEFAULT_UNSURE], found.notes)
    for run, unsure_dir in zip(screening, unsure):
        provisions.update(screened([run], [unsure_dir], found.notes))
    classified = classifications([CLASSIFICATION, *classification])
    print(f"  {len(provisions):,} provisions. Reading the texts of the kept ones ...", flush=True)
    flags, titles = check_provisions(found, provisions, classified)
    print("Document level, classification, summaries ...", flush=True)
    check_documents(found, document_decisions(DOCUMENT_RUN, found.notes), provisions, titles)
    check_classification(found, classified)
    check_summaries_v1(found, SUMMARIES_V1, flags, found.notes)
    check_summaries_v2(found, SUMMARIES_V2, flags, found.notes)
    out = args.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    summary = write_report(out, found)
    print("\nCounts:")
    for check, count in summary["counts"].items():
        print(f"  {check:32} {count:,}")
    print(f"\nReport: {out / 'report.md'}\nAll flagged items: {out / 'issues.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
