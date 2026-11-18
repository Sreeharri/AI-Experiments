"""Export the changes between EUR-Lex acts (amendments, repeals, corrigenda, ...) for the AI step.

Read-only. Reads the backend tables with SQL; no backend code is used or needed.

For every operational relationship "changing act -> changed act" where at least one of the two acts
is relevant (it has results in the classification), it writes one record per provision change, one
record per act-level relationship without provision detail, and one record per corrigendum:

    kind         PROVISION | ACT | CORRIGENDUM
    changing     {celex, title, date, entry_into_force, application, url}
    changed      {celex, title}
    relation     AMENDS, REPEALS, CORRECTS, ...
    operation    ADD, INSERT, REPLACE, DELETE, ... (PROVISION only)
    provision    "Article 15" (PROVISION only), root locator ("article", "15")
    effective_from
    old_text     the provision before the change (from the stored version before the date)
    new_text     the provision after the change (stored version from the date on, or the quoted new text)
    instruction  the passage of the changing act that makes this change
    final_articles  entry into force, application, transposition and transitional articles of the changing act
    corrections  [{location, wrong, correct}] (CORRIGENDUM only)
    wording      FULL | NEW_ONLY | OLD_ONLY | INSTRUCTION_ONLY | NONE

    python tools/export_eurlex_changes.py --count-only
    python tools/export_eurlex_changes.py --since 2015 --out runs/eurlex_changes_export
    python tools/export_eurlex_changes.py --celex 32010L0075 --out runs/eurlex_changes_ied
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
FORMAT = "eurlex_changes_v1"

# Relationships that change the text or the application of another act.
TEXT_CHANGES = {"AMENDS", "REPLACES", "REPEALS", "IMPLICITLY_REPEALS", "CORRECTS", "DEROGATES", "SUSPENDS",
                "PARTIALLY_SUSPENDS", "EXTENDS_APPLICATION", "EXTENDS_VALIDITY", "DEFERS_APPLICATION",
                "REESTABLISHES", "RENDERS_OBSOLETE"}
# Relationships that add rules next to another act without changing its text (shown as a link only).
LINKS_ONLY = {"COMPLETES", "ADDS_TO", "IMPLEMENTS", "INCORPORATES", "CONFIRMS", "ADOPTS", "PARTIALLY_ADOPTS"}

TEXT_LIMIT = 12000          # characters of old or new text kept per record
INSTRUCTION_LIMIT = 6000
FINAL_ARTICLE_LIMIT = 1500
MAX_FINAL_ARTICLES = 4

ROOT_PATTERN = re.compile(r"\b(Article|Annex|Artikel|Anhang)\s+([0-9]+[a-z]*|[IVXLCDM]+[a-z]*)\b", re.I)
FINAL_ARTICLE = re.compile(
    r"entry into force|enter into force|shall apply from|shall apply as from|application|transposition|"
    r"Member States shall bring into force|transitional|Inkrafttreten|Umsetzung|Übergang|gilt ab", re.I)
MARKERS = re.compile(r"[▼►◄]\s*(?:M\d+|B|C\d*|A\d+)\b")
QUOTE = re.compile(r"[‘“\"']\s*(.+?)\s*[’”\"']\s*(?=[;.:,]?\s*(?:\(\d+[a-z]?\)|$|\n|[a-z]\)))", re.S)
POINT_START = re.compile(r"(?:^|(?<=\s))\(\d+[a-z]?\)\s+(?=[A-Za-z‘])")
CORRECTION = re.compile(
    r"(?P<location>(?:On page|Page|Seite|Auf Seite)[^:]{0,200}?)?[:,]?\s*"
    r"(?:for|anstatt|statt)\s*:?\s*[‘“\"'](?P<wrong>.+?)[’”\"']\s*[,;]?\s*"
    r"(?:read|muss es heißen|lies)\s*:?\s*[‘“\"'](?P<correct>.+?)[’”\"']", re.S | re.I)


# ------------------------------------------------------------------ text helpers (no database)

def clean(text: str | None) -> str:
    """Remove publisher markers (▼M1 ...) and squash white space."""
    return re.sub(r"\s+", " ", MARKERS.sub(" ", text or "")).strip()


def signature(text: str | None) -> str:
    return re.sub(r"[^0-9a-z]", "", clean(text).casefold())


def root_of(locator: str | None) -> tuple[str, str] | None:
    """("article", "15") for "Article 15(3)(b)", ("annex", "ii") for "Annex II, point 2"."""
    match = ROOT_PATTERN.search(locator or "")
    if not match:
        return None
    kind = "article" if match.group(1).casefold() in {"article", "artikel"} else "annex"
    return kind, match.group(2).casefold()


def act_number(celex: str) -> str | None:
    """The number the act is cited by: 32010L0075 -> 2010/75."""
    match = re.match(r"^[0-9](\d{4})[A-Z]{1,2}(\d{4})", celex or "")
    return f"{match.group(1)}/{int(match.group(2))}" if match else None


def mentions(text: str, root: tuple[str, str]) -> bool:
    kind, number = root
    words = "Article|Artikel" if kind == "article" else "Annex|Anhang"
    return bool(re.search(rf"\b(?:{words})\s+{re.escape(number)}(?![0-9a-z])", text, re.I))


def split_points(text: str) -> list[str]:
    """Split an amending article into its numbered points (1), (2), ... but never inside quoted new text."""
    cuts = [0]
    for match in POINT_START.finditer(text):
        before = text[:match.start()]
        if before.count("‘") - before.count("’") <= 0 and before.count("“") - before.count("”") <= 0:
            cuts.append(match.start())
    cuts.append(len(text))
    return [text[a:b].strip() for a, b in zip(cuts, cuts[1:]) if text[a:b].strip()]


def instruction_for(elements: list[dict], changed_celex: str, root: tuple[str, str] | None) -> tuple[str, str | None]:
    """(text, element identifier) of the changing act's passage that changes `root` of the changed act."""
    number = act_number(changed_celex)
    citing = [e for e in elements if number and number in e["text"]] or elements
    if root is None:
        return "", None
    for element in citing:
        points = split_points(element["text"])
        hits = [p for p in points if mentions(p[:160], root)]
        if hits:
            return " ".join(hits)[:INSTRUCTION_LIMIT], element["identifier"]
    for element in citing:  # the whole element if the locator only stands further in
        if mentions(element["text"], root):
            return element["text"][:INSTRUCTION_LIMIT], element["identifier"]
    return "", None


def quoted_new_text(instruction: str, operation: str) -> str:
    """The text inside quotes after 'replaced by the following' / 'inserted' / 'added'."""
    if operation not in {"ADD", "INSERT", "REPLACE", "MODIFY"}:
        return ""
    trigger = re.search(r"(replaced by the following|is replaced by|are replaced by|following [^:]{0,80} (?:is|are) "
                        r"(?:inserted|added)|(?:is|are) (?:inserted|added)[^:]{0,40}:|shall read)", instruction, re.I)
    if not trigger:
        return ""
    found = QUOTE.findall(instruction[trigger.end():])
    return clean(" ".join(found))[:TEXT_LIMIT]


def corrections_in(text: str) -> list[dict]:
    found = []
    for match in CORRECTION.finditer(text):
        found.append({"location": clean(match.group("location") or "")[:200],
                      "wrong": clean(match.group("wrong"))[:1500], "correct": clean(match.group("correct"))[:1500]})
    return found


def final_articles(elements: list[dict]) -> list[dict]:
    """Articles of the changing act on entry into force, application, transposition and transition."""
    chosen = []
    for element in elements:
        if not element["identifier"].casefold().startswith(("article", "artikel")):
            continue
        head = f"{element.get('heading') or ''} {element['text'][:300]}"
        if FINAL_ARTICLE.search(head):
            chosen.append({"locator": element["identifier"], "text": element["text"][:FINAL_ARTICLE_LIMIT]})
    return chosen[-MAX_FINAL_ARTICLES:]


def version_day(version: dict) -> date | None:
    value = version.get("version_date") or version.get("effective_from")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def pick_versions(versions: list[dict], day: date | None) -> tuple[dict | None, dict | None]:
    """(version before the change, version from the change on). Never guesses without dates."""
    dated = sorted((v for v in versions if version_day(v)), key=version_day)
    if day is None or not dated:
        return None, None
    before = [v for v in dated if version_day(v) < day]
    after = [v for v in dated if version_day(v) >= day]
    return (before[-1] if before else None), (after[0] if after else None)


def element_text(elements: list[dict], root: tuple[str, str] | None) -> str | None:
    """Text of the whole article or annex `root` in one version (the shallowest matching element)."""
    if root is None:
        return None
    matches = [e for e in elements if root_of(e["identifier"]) == root
               and re.fullmatch(r"\s*(?:Article|Annex|Artikel|Anhang)\s+[0-9IVXLCDMa-z]+\.?\s*", e["identifier"], re.I)]
    if not matches:
        return None
    best = min(matches, key=lambda e: (e["depth"], len(e["path"] or "")))
    return best["text"][:TEXT_LIMIT] or None


def wording_of(old: str | None, new: str | None, instruction: str) -> str:
    if old and new:
        return "FULL"
    if new:
        return "NEW_ONLY"
    if old:
        return "OLD_ONLY"
    return "INSTRUCTION_ONLY" if instruction else "NONE"


def record_id(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()[:20]


def year_of(celex: str, day) -> int | None:
    if day:
        return day.year if isinstance(day, date) else int(str(day)[:4])
    match = re.match(r"^[0-9](\d{4})", celex or "")
    return int(match.group(1)) if match else None


# ------------------------------------------------------------------ database

RELATIONSHIPS_SQL = """
SELECT r.id::text AS id, r.relation_type, r.relation_family, r.status, r.valid_from, r.valid_to,
       COALESCE(ls.celex, sc.external_id) AS changing_celex, COALESCE(lt.celex, tc.external_id) AS changed_celex,
       r.source_document_id::text AS changing_document, r.target_document_id::text AS changed_document,
       COALESCE(sc.title_en, sc.canonical_title, sc.title_de) AS changing_title,
       COALESCE(tc.title_en, tc.canonical_title, tc.title_de) AS changed_title,
       COALESCE(sc.document_date, sc.publication_date) AS changing_date,
       sc.date_entry_into_force AS entry_into_force, sc.date_application AS application,
       sc.information_url AS changing_url, tc.information_url AS changed_url
FROM instrument_relationships r
JOIN source_documents sc ON sc.id = r.source_document_id
JOIN source_documents tc ON tc.id = r.target_document_id
LEFT JOIN legal_instruments ls ON ls.source_document_id = r.source_document_id
LEFT JOIN legal_instruments lt ON lt.source_document_id = r.target_document_id
WHERE r.relation_family = 'OPERATIONAL'
"""
CHANGES_SQL = """
SELECT id::text AS id, relationship_id::text AS relationship_id, ordinal, operation, raw_operation_code,
       target_provision, target_locator_type, target_locator_number, target_element_id::text AS target_element_id,
       resolution_status, raw_annotation, effective_from, effective_to
FROM relationship_provision_changes WHERE relationship_id = ANY(%(ids)s::uuid[]) ORDER BY relationship_id, ordinal
"""
COMPARISONS_SQL = """
SELECT change_id::text AS change_id, old_text, new_text, instruction_text
FROM act_wording_comparisons WHERE change_id = ANY(%(ids)s::uuid[]) AND upper(language) = 'EN'
"""
VERSIONS_SQL = """
SELECT v.id::text AS id, v.version_kind, v.version_date, v.effective_from, v.is_current, v.is_preferred, v.language
FROM instrument_versions v JOIN legal_instruments li ON li.id = v.instrument_id
WHERE li.source_document_id = %(document)s::uuid AND upper(v.language) = 'EN'
"""
ELEMENTS_SQL = """
SELECT version_id::text AS version_id, element_type, identifier, heading, body_text, path, depth, ordinal
FROM legal_elements WHERE version_id = ANY(%(ids)s::uuid[]) ORDER BY version_id, ordinal
"""


class Store:
    """Read-only access with a small cache per document."""

    def __init__(self, connection):
        self.connection = connection
        self.cache: dict[str, tuple[list[dict], dict[str, list[dict]]]] = {}

    def rows(self, sql, **params):
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params or None)
            return cursor.fetchall()

    def document(self, document_id: str):
        """(versions, {version id: elements}) of one document, EN only."""
        if document_id not in self.cache:
            if len(self.cache) > 200:
                self.cache.clear()
            versions = self.rows(VERSIONS_SQL, document=document_id)
            elements = defaultdict(list)
            if versions:
                for row in self.rows(ELEMENTS_SQL, ids=[v["id"] for v in versions]):
                    elements[row["version_id"]].append({
                        "identifier": row["identifier"] or "", "heading": row["heading"],
                        "text": clean(row["body_text"]), "path": row["path"], "depth": row["depth"] or 0})
            self.cache[document_id] = (versions, elements)
        return self.cache[document_id]

    def main_version(self, document_id: str) -> list[dict]:
        """Root elements of the version to read a changing act from (preferred, current, original)."""
        versions, elements = self.document(document_id)
        if not versions:
            return []
        best = max(versions, key=lambda v: (bool(v["is_preferred"]), bool(v["is_current"]),
                                            v["version_kind"] == "ORIGINAL", len(elements.get(v["id"], []))))
        items = elements.get(best["id"], [])
        roots = [e for e in items if root_of(e["identifier"]) and
                 re.fullmatch(r"\s*(?:Article|Annex|Artikel|Anhang)\s+[0-9IVXLCDMa-z]+\.?\s*", e["identifier"], re.I)]
        return roots or items


# ------------------------------------------------------------------ building the records

def changing_part(rel) -> dict:
    return {"celex": rel["changing_celex"], "title": rel["changing_title"] or "", "date": rel["changing_date"],
            "entry_into_force": rel["entry_into_force"], "application": rel["application"],
            "url": rel["changing_url"]}


def build_records(store: Store, relationships: list[dict], say=print) -> tuple[list[dict], Counter]:
    gaps = Counter()
    records = []
    changes = defaultdict(list)
    ids = [rel["id"] for rel in relationships]
    for start in range(0, len(ids), 2000):
        for row in store.rows(CHANGES_SQL, ids=ids[start:start + 2000]):
            changes[row["relationship_id"]].append(row)
    change_ids = [row["id"] for rows in changes.values() for row in rows]
    reviewed = {}
    for start in range(0, len(change_ids), 2000):
        for row in store.rows(COMPARISONS_SQL, ids=change_ids[start:start + 2000]):
            reviewed[row["change_id"]] = row

    for number, rel in enumerate(relationships, 1):
        if number % 200 == 0:
            say(f"  {number:,}/{len(relationships):,} relationships")
        changing_elements = store.main_version(rel["changing_document"])
        finals = final_articles(changing_elements)
        base = {"relation": rel["relation_type"], "relationship_id": rel["id"], "status": rel["status"],
                "changing_document_id": rel["changing_document"], "changed_document_id": rel["changed_document"],
                "changing": changing_part(rel),
                "changed": {"celex": rel["changed_celex"], "title": rel["changed_title"] or "", "url": rel["changed_url"]},
                "final_articles": finals, "valid_from": rel["valid_from"], "valid_to": rel["valid_to"]}
        if not changing_elements:
            gaps["changing_act_without_text"] += 1

        if rel["relation_type"] == "CORRECTS":
            text = " ".join(e["text"] for e in changing_elements)
            found = corrections_in(text)
            gaps["corrigenda"] += 1
            gaps["corrigenda_without_parsed_corrections"] += not found
            records.append({**base, "kind": "CORRIGENDUM", "id": record_id("C", rel["id"]),
                            "corrections": found, "instruction": "" if found else text[:INSTRUCTION_LIMIT],
                            "languages": "EN" if changing_elements else ""})
            continue

        rows = changes.get(rel["id"], [])
        if not rows:  # act-level only: whole act repealed, application deferred, ...
            number_ = act_number(rel["changed_celex"])
            citing = [e for e in changing_elements if number_ and number_ in e["text"]]
            text = " ".join(e["text"] for e in citing)[:INSTRUCTION_LIMIT]
            gaps["act_level_relationships"] += 1
            records.append({**base, "kind": "ACT", "id": record_id("A", rel["id"]), "operation": rel["relation_type"],
                            "instruction": text, "instruction_locator": citing[0]["identifier"] if citing else None,
                            "wording": "INSTRUCTION_ONLY" if text else "NONE"})
            continue

        versions, elements = store.document(rel["changed_document"])
        for row in rows:
            locator = row["target_provision"] or " ".join(filter(None, [row["target_locator_type"],
                                                                         row["target_locator_number"]]))
            root = root_of(locator)
            raw = row["raw_annotation"] or {}
            day = row["effective_from"] or _date(raw.get("START_OF_VALIDITY")) or rel["valid_from"] \
                or rel["entry_into_force"]
            operation = (row["operation"] or "OTHER").upper()
            instruction, instruction_locator = instruction_for(changing_elements, rel["changed_celex"], root)
            old = new = None
            source = "NONE"
            if row["id"] in reviewed:
                stored = reviewed[row["id"]]
                old, new = clean(stored["old_text"]) or None, clean(stored["new_text"]) or None
                instruction = clean(stored["instruction_text"]) or instruction
                source = "REVIEWED"
            else:
                before, after = pick_versions(versions, _date(day))
                if before is not None and operation not in {"ADD", "INSERT"}:
                    old = element_text(elements.get(before["id"], []), root)
                if after is not None and operation not in {"DELETE", "REPEAL"}:
                    new = element_text(elements.get(after["id"], []), root)
                if old and new and signature(old) == signature(new):
                    old = new = None            # same text: the stored versions do not show this change
                    gaps["same_text_in_versions"] += 1
                if not new and operation not in {"DELETE", "REPEAL"}:
                    new = quoted_new_text(instruction, operation) or None
                    source = "INSTRUCTION_QUOTE" if new else source
                source = "VERSIONS" if (old or new) and source == "NONE" else source
            wording = wording_of(old, new, instruction)
            gaps[f"wording_{wording}"] += 1
            gaps["target_not_resolved"] += root is None
            gaps["instruction_not_found"] += not instruction
            gaps["no_effective_date"] += day is None
            records.append({**base, "kind": "PROVISION", "id": record_id("P", row["id"]), "change_id": row["id"],
                            "ordinal": row["ordinal"], "operation": operation, "provision": locator,
                            "root": list(root) if root else None, "target_element_id": row["target_element_id"],
                            "resolution_status": row["resolution_status"], "effective_from": day,
                            "effective_to": row["effective_to"], "old_text": old, "new_text": new,
                            "instruction": instruction, "instruction_locator": instruction_locator,
                            "wording": wording, "wording_source": source})
    return records, gaps


def _date(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def in_scope(rel: dict, relevant: set[str], since: int | None, celex: set[str]) -> bool:
    if celex:
        return rel["changing_celex"] in celex or rel["changed_celex"] in celex
    if rel["changing_celex"] not in relevant and rel["changed_celex"] not in relevant:
        return False
    year = year_of(rel["changing_celex"], rel["changing_date"])
    return since is None or (year is not None and year >= since)


def plain(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    parser.add_argument("--classification", type=Path, default=ROOT / "runs/eurlex_classification_v5")
    parser.add_argument("--out", type=Path, default=ROOT / "runs/eurlex_changes_export")
    parser.add_argument("--since", type=int, default=2015, help="Changing acts from this year on (0 = all).")
    parser.add_argument("--celex", nargs="+", default=[], help="Only relationships of these acts (any year).")
    parser.add_argument("--count-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        from dotenv import dotenv_values
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        parser.exit(1, f"Missing package: {exc}\n")
    results_file = args.classification / "results.json"
    if not results_file.is_file() and not args.celex:
        parser.exit(1, f"Classification results not found: {results_file}\n")
    relevant = set(json.loads(results_file.read_text(encoding="utf-8"))) if results_file.is_file() else set()
    values = dotenv_values(args.env) if args.env.exists() else {}
    url = os.environ.get("DATABASE_URL") or values.get("DATABASE_URL")
    if not url:
        parser.exit(1, "DATABASE_URL is missing from the .env\n")
    for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://"):
        url = url.replace(prefix, "postgresql://", 1)
    out = args.out
    if not args.count_only and (out / "records.jsonl").exists():
        parser.exit(1, f"{out} already holds an export. Use a new --out folder.\n")
    celex = {value.strip().upper() for value in args.celex}
    since = args.since or None

    try:
        with psycopg.connect(url, row_factory=dict_row, connect_timeout=20) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            connection.execute("SET LOCAL statement_timeout = '30min'")
            store = Store(connection)
            everything = store.rows(RELATIONSHIPS_SQL)
            chosen = [rel for rel in everything if in_scope(rel, relevant, since, celex)]
            text_changes = [rel for rel in chosen if rel["relation_type"] in TEXT_CHANGES]
            links = [rel for rel in chosen if rel["relation_type"] in LINKS_ONLY]
            print(f"{len(everything):,} operational relationships in the database. In scope: {len(chosen):,} "
                  f"({len(text_changes):,} that change text or application, {len(links):,} links only).", flush=True)
            print("By type:", dict(Counter(rel["relation_type"] for rel in chosen).most_common()), flush=True)
            if args.count_only:
                return 0
            records, gaps = build_records(store, text_changes)
    except Exception as exc:  # noqa: BLE001 - never print a driver message that may hold the connection string
        detail = str(exc).splitlines()[0][:200] if not isinstance(exc, OSError) else ""
        parser.exit(1, f"Export failed ({type(exc).__name__}). {detail}\n")

    out.mkdir(parents=True, exist_ok=True)
    temp = out / "records.jsonl.tmp"
    with temp.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, default=plain) + "\n")
    temp.replace(out / "records.jsonl")
    link_rows = [{"relation": rel["relation_type"], "changing": changing_part(rel),
                  "changed": {"celex": rel["changed_celex"], "title": rel["changed_title"] or ""}} for rel in links]
    (out / "links.json").write_text(json.dumps(link_rows, ensure_ascii=False, indent=1, default=plain), encoding="utf-8")
    summary = {"format": FORMAT, "created_at": datetime.now(timezone.utc).isoformat(), "since": since,
               "celex": sorted(celex), "relevant_acts": len(relevant), "relationships_in_scope": len(chosen),
               "records": len(records), "by_kind": dict(Counter(r["kind"] for r in records)),
               "by_relation": dict(Counter(r["relation"] for r in records)),
               "by_operation": dict(Counter(r.get("operation") for r in records if r["kind"] == "PROVISION")),
               "gaps": dict(gaps), "links_only": len(links)}
    (out / "export_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("records", "by_kind", "gaps")}, ensure_ascii=False, indent=1))
    print(f"Saved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
