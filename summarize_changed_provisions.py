"""Summary version 2 for the relevant provisions that EUR-Lex changed.

Input:
- the export of the backend (python -m app.cli.export_changed_texts), folder
  reports/changed-texts/<time>/: per relevant act each changed article or annex with its text in
  force today and its changes (latest from 2015 on and upcoming with old and new wording,
  earlier ones as one line);
- the classification (runs/eurlex_classification_v5): which provisions are relevant.

For every relevant article or annex that was changed (one unit per article or annex):
1. Summary version 2: written like version 1 (same prompt, same format: headline, paragraph,
   up to 4 key points, "Weitere Punkte"), but from today's text. A long text is read in parts
   as in version 1; the parts that hold the latest change are read first.
2. "Was hat sich geändert": fact lines without AI (date, place, what happened, amending act)
   and one or two sentences by Qwen, written only from the stored old and new wording of the
   latest change (no request when there is no wording).
3. Upcoming changes: one line each ("Ab 01.01.2027: ... wird ersetzt"), not mixed into the
   summary.

Not asked again: a deleted article ("Gestrichen am ..."), an article without a newer text
(today's text is the original act: version 1 stays), and tables: a relevant table of a changed
annex keeps its version-1 summary and gets the annex's "Was hat sich geändert".

Version 1 is not changed. Output folder (default runs/eurlex_summaries_v2_changes):
    units/<CELEX>/<article or annex>.json   one record per changed article or annex
    changed_provisions.json                 all records, for the database import and the UI
    review.csv                              one row per record, to check by hand
    preview/index.html                      the act pages as the UI should show them
    summary.json                            counts

Ctrl+C stops; the same command continues. When Qwen cannot be reached it waits as the other
steps do.

    python summarize_changed_provisions.py --changed-dir <backend>/reports/changed-texts/<time> --count-only
    python summarize_changed_provisions.py --changed-dir <backend>/reports/changed-texts/<time> --sample 20
    python summarize_changed_provisions.py --changed-dir <backend>/reports/changed-texts/<time>
"""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date
import html
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import summarize_eurlex_provisions as base  # noqa: E402
from classify_eurlex_provisions import (  # noqa: E402
    KEPT, MAX_WORKERS, ConnectionLost, Unclassified, reachable, read_json, run_until_done, safe_name,
    squash, wait_until_reachable, write_json)
from eurlex_provision.simple import TARGET_CHARS, split_target, target_text  # noqa: E402
from lr_ai.qwen_client import QwenClient  # noqa: E402

CHANGE_PROMPT = ROOT / "prompts/eurlex_change_summary.md"
DEFAULT_CLASSIFICATION = ROOT / "runs/eurlex_classification_v5"
DEFAULT_OUTPUT = ROOT / "runs/eurlex_summaries_v2_changes"
VERSION = "changes-v1"
CHANGE_TASK = "TASK: Write what changed. Return the JSON object described in the instructions."
CHANGE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["sentence"],
                 "properties": {"sentence": {"type": "string"}}}
MAX_PAIRS = 6               # changed places of one day sent to Qwen
MAX_WORDING = 3000          # characters of old and of new wording per place
MAX_FACT_LINES = 8          # fact lines shown per article; the rest is counted
ROOT_LOCATOR = re.compile(r"^(Article|Annex)\b\s*([0-9]+[a-z]*|[IVXLC]+[a-z]*)?\b", re.I)

# ------------------------------------------------------------------ words for the fact lines

OPERATION_DE = {"REPLACE": "ersetzt", "MODIFY": "geändert", "ADD": "eingefügt", "INSERT": "eingefügt",
                "DELETE": "gestrichen", "REPEAL": "aufgehoben", "OTHER": "geändert"}
RELATION_DE = {"SUSPENDS": "ausgesetzt", "PARTIALLY_SUSPENDS": "teilweise ausgesetzt",
               "DEROGATES": "Ausnahme vorgesehen", "DEFERS_APPLICATION": "Anwendung verschoben",
               "RENDERS_OBSOLETE": "gegenstandslos", "REESTABLISHES": "wieder in Kraft gesetzt",
               "INCORPORATES": "übernommen"}
TOKEN_DE = {"paragraph": "Absatz", "subpoint": "Ziffer", "part": "Teil", "table": "Tabelle",
            "appendix": "Anlage", "title": "Überschrift", "unnumbered paragraph": "Unterabsatz"}
TOKEN = re.compile(r"\b(unnumbered paragraph|paragraph|subpoint|point|part|table|appendix|title)\b\s*(\S*)")


def roman(number):
    values = [(10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
    out = ""
    for value, letters in values:
        while number >= value:
            out, number = out + letters, number - value
    return out


def german_place(place):
    """'Article 6 paragraph 5 point b' -> 'Artikel 6 Absatz 5 Buchstabe b'."""
    if not place:
        return ""
    head = re.sub(r"^Article\b", "Artikel", re.sub(r"^Annex\b", "Anhang", place.strip()))
    match = TOKEN.search(head)
    start, rest = (head[:match.start()].strip(), head[match.start():]) if match else (head, "")
    words = []
    for kind, number in TOKEN.findall(rest):
        if kind == "point":
            word = "Buchstabe" if re.fullmatch(r"[a-z]{1,3}", number) else "Nummer"
        else:
            word = TOKEN_DE[kind]
        words.append(f"{word} {number}".strip())
    return " ".join([start, *words]).strip()


def act_name(celex):
    """'32024R1257' -> 'Verordnung 2024/1257'; other CELEX numbers stay as they are."""
    kind = base.act_type(celex, "de")
    match = base.CELEX_PARTS.fullmatch(celex or "")
    if not kind or not match:
        return f"Rechtsakt {celex}"
    year, number = int(match.group(1)), str(int(match.group(3)))
    if match.group(2) == "R" and year < 2015:
        return f"{kind} Nr. {number}/{year}"
    return f"{kind} {year}/{number}"


def day_de(text):
    try:
        day = date.fromisoformat(text)
    except (TypeError, ValueError):
        return text or "ohne Datum"
    return f"{day:%d.%m.%Y}"


def version_de(label):
    """'version 2026-09-23' -> 'Fassung vom 23.09.2026'; 'original text' -> 'ursprünglicher Text'."""
    if (label or "").startswith("version "):
        return f"Fassung vom {day_de(label.split()[-1])}"
    return "ursprünglicher Text" if label == "original text" else (label or "")


def what_happened(line):
    if line.get("status") == "NO_WORDING_NEEDED" or line["operation"] not in OPERATION_DE:
        for relation, word in RELATION_DE.items():
            if (line.get("relation") or "").startswith(relation):
                return word
        if (line.get("relation") or "").startswith("EXTENDS"):
            return "verlängert"
    return OPERATION_DE.get(line["operation"], "geändert")


def fact_line(line):
    """'07.07.2024 – Absatz 3 ersetzt durch Verordnung 2024/1257' (no AI)."""
    place = german_place(line.get("place")) or line.get("where") or "die Vorschrift"
    return f"{day_de(line['date'])} – {place} {what_happened(line)} durch {act_name(line['changing_act'])}"


def upcoming_line(line):
    place = german_place(line.get("place")) or line.get("where") or "die Vorschrift"
    return f"Ab {day_de(line['date'])}: {place} wird {what_happened(line)} ({act_name(line['changing_act'])})"


# ------------------------------------------------------------------ which provisions

def root_of_locator(locator):
    """('annex', 'i') of a relevant provision such as 'Annex I [source path annex/i]'; None for others."""
    text = base.plain_locator(locator)
    match = ROOT_LOCATOR.match(text)
    if not match:
        return None
    if match.group(2) is None and re.match(r"\s*[^\s,\[]", text[match.end():]):
        return None  # a named annex ("Annex B-03 ..."): not matched by number
    kind, number = match.group(1).lower(), (match.group(2) or "sole").lower()
    return kind, number


def find_root(roots, kind, number):
    """The key of the changed article or annex in the export, also when one side writes the annex
    number in Roman and the other in Arabic numbers, or the only annex without a number."""
    keys = [f"{kind}/{number}"]
    if number.isdigit():
        keys.append(f"{kind}/{roman(int(number))}")
    elif re.fullmatch(r"[ivxlc]+", number):
        keys.append(f"{kind}/{base.roman_value(number.upper())}")
    for key in keys:
        if key in roots:
            return key
    annexes = [key for key in roots if key.startswith("annex/")]
    if kind == "annex" and number == "sole" and len(annexes) == 1:
        return annexes[0]
    return None


def merged(folders):
    """The provision classifications of several folders, by act; the first folder wins for a provision
    that is in more than one."""
    result = {}
    for folder in folders:
        path = Path(folder) / "results.json"
        if folder != folders[0] and not path.is_file():
            continue  # an extra classification that was not written (nothing relevant was found)
        for celex, provisions in read_json(path).items():
            for locator, value in provisions.items():
                result.setdefault(celex, {}).setdefault(locator, value)
    return result


def plan(changed_dir, classified, levels, celexes=()):
    """[unit] for every changed article or annex that holds a relevant provision, and the
    counts of what was matched."""
    index = read_json(changed_dir / "index.json")
    units, facts = [], Counter()
    for celex in sorted(index):
        relevant = {loc: v for loc, v in classified.get(celex, {}).items() if v.get("level") in levels}
        if not relevant or (celexes and celex not in celexes):
            continue
        record = read_json(changed_dir / "acts" / f"{celex}.json")
        roots = record["roots"]
        matched = defaultdict(lambda: {"provisions": [], "tables": []})
        for locator, result in sorted(relevant.items()):
            parsed = root_of_locator(locator)
            key = find_root(roots, *parsed) if parsed else None
            if key is None:
                facts["relevant provisions not changed"] += 1
                continue
            group = "tables" if base.TABLE_PIECE.match(base.plain_locator(locator)) else "provisions"
            matched[key][group].append({"locator": locator, "level": result.get("level"),
                                        "departments": result.get("departments", []),
                                        "domain": result.get("primary_domain")})
            facts[f"relevant {group} changed"] += 1
        for key, found in sorted(matched.items()):
            root = roots[key]
            units.append({"celex": celex, "title": record.get("title", ""), "act_level": record.get("level"),
                          "today_version": record.get("today_version"),
                          "today_version_celex": record.get("today_version_celex"), "key": key,
                          "root": root, **found})
    return units, facts


def unit_kind(unit):
    """summary: version 2 is written; deleted; same_text: today's text is the original act (no
    newer text, version 1 stays); no_text: the article could not be cut out of today's text;
    tables: only tables of the annex are relevant."""
    root = unit["root"]
    if root.get("deleted"):
        return "deleted"
    if not unit["provisions"]:
        return "tables"
    if not root.get("today"):
        return "no_text"
    if (root["today"].get("version_kind") or "") != "CONSOLIDATED":
        return "same_text"
    return "summary"


# ------------------------------------------------------------------ the requests

def text_node(unit):
    today = unit["root"]["today"]
    return {"locator": unit["root"]["name"], "heading": today.get("heading") or "", "text": today["text"]}


def saved_pack(unit, node):
    """The shape summarise_provision() reads: the act, today's text, and which parts to read
    first (those that hold the wording of the latest change)."""
    pack = {"document": {"celex": unit["celex"], "title": unit["title"]}, "provision": node,
            "essential_context": [], "direct_references": [], "footnotes": [], "unavailable_references": []}
    parts = split_target(node, TARGET_CHARS)
    saved = {"evidence_pack": pack}
    if len(parts) > 1:
        snippets = [squash(line.get("new_text") or "")[:80] for line in unit["root"]["changes"]
                    if line["role"] in ("latest", "upcoming") and line.get("new_text")]
        texts = [squash(" ".join(target_text(part))) for part in parts]
        first = [index for index, text in enumerate(texts) if any(s and s in text for s in snippets)]
        chosen = (first + [index for index in range(len(parts)) if index not in first])[:base.MAX_PARTS]
        saved["model_attempt"] = {"parts": [
            {"part": str(index + 1), "decision": {"level": "DIRECT" if index in chosen else "IGNORE"}}
            for index in range(len(parts))]}
    return saved


def latest_lines(unit):
    return [line for line in unit["root"]["changes"] if line["role"] == "latest"]


def change_input(unit):
    """What Qwen sees for the 'Was hat sich geändert' sentence, or None when there is no wording."""
    pairs, more = [], 0
    for line in latest_lines(unit):
        old, new = line.get("old_text"), line.get("new_text")
        if not (old or new):
            continue
        if len(pairs) >= MAX_PAIRS:
            more += 1
            continue
        item = {"place": line.get("place") or line.get("where"),
                "type": {"REPLACE": "replaced", "ADD": "added", "INSERT": "added", "DELETE": "deleted",
                         "REPEAL": "deleted"}.get(line["operation"], "changed")}
        if old and not (line.get("old_is_context") and len(old) > MAX_WORDING):
            item["old_wording"] = old[:MAX_WORDING]
            if line.get("old_is_context"):
                item["old_is_context"] = True
        if new:
            item["new_wording"] = new[:MAX_WORDING]
        if "old_wording" in item or "new_wording" in item:
            pairs.append(item)
    if not pairs:
        return None
    sent = {"act": {"celex": unit["celex"], "title": unit["title"]}, "provision": unit["root"]["name"],
            "changes": pairs}
    if more:
        sent["more_changes"] = more
    return sent


def validate_change(reply, source):
    if not isinstance(reply, dict) or not isinstance(reply.get("sentence"), str) or not squash(reply["sentence"]):
        raise Unclassified("The reply must hold the sentence.")
    answer = {"sentence": squash(reply["sentence"])}
    marks = base.number_marks([answer["sentence"]], source)
    if marks:
        answer["number_check"] = marks
    return answer


def change_sentence(client, system, sent):
    log = []
    source = json.dumps(sent, ensure_ascii=False)
    answer = base.ask(client, system, sent, CHANGE_TASK, CHANGE_SCHEMA, "change_summary",
                      lambda reply: validate_change(reply, source), log, 600)
    return answer, log


def what_changed(unit):
    """The fact lines (no AI) of the latest change, or of the last change before --since."""
    lines = latest_lines(unit)
    if lines:
        facts = [fact_line(line) for line in lines[:MAX_FACT_LINES]]
        if len(lines) > MAX_FACT_LINES:
            facts.append(f"und {len(lines) - MAX_FACT_LINES} weitere Stellen am selben Tag")
        return facts
    history = [line for line in unit["root"]["changes"] if line["role"] == "history"]
    if history:
        last = history[0]  # newest first
        return [f"Zuletzt geändert am {day_de(last['date'])} durch {act_name(last['changing_act'])}"]
    return []


def summarise_unit(client, systems, unit):
    """The record of one changed article or annex."""
    root = unit["root"]
    kind = unit_kind(unit)
    record = {key: unit[key] for key in ("celex", "title", "act_level", "today_version", "today_version_celex",
                                          "key", "provisions", "tables")}
    record.update(version=VERSION, kind=kind, name=root["name"], today=_today_facts(root),
                  deleted=root.get("deleted"),
                  what_changed={"facts": what_changed(unit)},
                  upcoming=[upcoming_line(line) for line in root["changes"] if line["role"] == "upcoming"],
                  changes={role: [_card(line) for line in root["changes"] if line["role"] == role]
                           for role in ("upcoming", "latest", "history", "no date")})
    if kind == "deleted":
        removed = root["deleted"]
        record["what_changed"]["facts"] = [f"Gestrichen am {day_de(removed['date'])} durch {act_name(removed['by'])}"]
    if kind == "summary":
        summary, record["audit"] = base.summarise_provision(
            client, systems["provision"], saved_pack(unit, text_node(unit)), systems["merge"])
        # Tidied as the version-1 cards are (point kinds, fixed German terms); nothing is cut.
        record["summary"] = base.tidy_summary(summary, root["today"]["text"][:600], base.OUTPUT_LANGUAGE)
    sent = change_input(unit) if kind != "deleted" else None
    if sent is not None:
        answer, log = change_sentence(client, systems["change"], sent)
        if base.OUTPUT_LANGUAGE:
            answer["sentence"] = base.fix_terms(answer["sentence"], base.OUTPUT_LANGUAGE)
        record["what_changed"].update(answer)
        record.setdefault("audit", {})["change_requests"] = log
    return record


def _today_facts(root):
    today = root.get("today") or {}
    return {key: today.get(key) for key in ("version_date", "version_kind", "link", "heading")} | {
        "characters": len(today.get("text") or "")}


def _card(line):
    keep = ("role", "date", "operation", "relation", "place", "where", "changing_act", "changing_act_link",
            "status", "complete", "note", "old_text", "new_text", "old_is_context", "old_link", "new_link")
    card = {key: line.get(key) for key in keep if line.get(key) not in (None, "")}
    card["line"] = upcoming_line(line) if line["role"] == "upcoming" else fact_line(line)
    return card


# ------------------------------------------------------------------ output

def unit_path(out, unit):
    return out / "units" / safe_name(unit["celex"]) / f"{unit['key'].replace('/', '_')}.json"


def saved_record(out, unit):
    path = unit_path(out, unit)
    record = read_json(path) if path.is_file() else {}
    return record if record.get("version") == VERSION and "failure" not in record else None


REVIEW_FIELDS = ["celex", "provision", "relevant", "level", "kind", "today_version", "headline", "overview",
                 "bullets", "was_hat_sich_geaendert", "sentence", "upcoming", "number_check", "failure",
                 "summary correct (yes/no)", "what changed correct (yes/no)", "comment"]


def export(out, units):
    records, rows, counts = [], [], Counter()
    for unit in units:
        path = unit_path(out, unit)
        record = read_json(path) if path.is_file() else None
        if record is None:
            counts["not done yet"] += 1
            continue
        records.append(record)
        counts[record.get("kind", "?")] += 1
        counts["failed"] += "failure" in record
        summary = record.get("summary") or {}
        changed = record.get("what_changed") or {}
        rows.append({
            "celex": record["celex"], "provision": record["name"],
            "relevant": " | ".join(p["locator"] for p in record["provisions"] + record["tables"]),
            "level": ",".join(sorted({p["level"] for p in record["provisions"] + record["tables"] if p.get("level")})),
            "kind": record.get("kind"), "today_version": record.get("today_version"),
            "headline": summary.get("headline", ""), "overview": summary.get("overview", ""),
            "bullets": " | ".join(f"{b['kind']}: {b['text']}" for b in summary.get("bullets", [])),
            "was_hat_sich_geaendert": " | ".join(changed.get("facts", [])),
            "sentence": changed.get("sentence", ""), "upcoming": " | ".join(record.get("upcoming", [])),
            "number_check": "; ".join(str(m) for m in [*summary.get("number_check", []),
                                                        *changed.get("number_check", [])]),
            "failure": record.get("failure", "")})
    write_json(out / "changed_provisions.json", records)
    with (out / "review.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    preview(out, records)
    summary = {"units": len(units), **counts}
    write_json(out / "summary.json", summary)
    return summary


STYLE = """body{font-family:Segoe UI,Arial,sans-serif;margin:24px auto;max-width:980px;color:#1c2430;background:#f5f6f8}
.unit{background:#fff;border:1px solid #d9dde3;border-radius:8px;padding:14px 18px;margin:14px 0}
h1{font-size:22px}h2{font-size:17px;margin:0 0 4px}.meta{color:#5b6573;font-size:13px}
.changed{background:#eef6ff;border-left:4px solid #2f6fdf;padding:8px 12px;margin:10px 0}
.upcoming{background:#fff4e5;border-left:4px solid #e08a00;padding:6px 12px;margin:6px 0}
.card{border:1px solid #e3e6ea;border-radius:6px;padding:8px 10px;margin:8px 0;font-size:14px}
.old{background:#fdecec;color:#8a1c1c;text-decoration:line-through;white-space:pre-wrap;padding:6px}
.context{background:#f4f4f4;color:#444;white-space:pre-wrap;padding:6px}
.new{background:#e9f7ec;color:#14532d;white-space:pre-wrap;padding:6px}
details{margin-top:8px}a{color:#2f6fdf}.check{color:#a15c00;font-size:12px}"""
KIND_NOTE = {"same_text": "Kein neuerer Text: die bisherige Zusammenfassung gilt weiter.",
             "no_text": "Der heutige Text konnte nicht ausgeschnitten werden: die bisherige Zusammenfassung gilt "
                        "weiter, bitte den Originaltext öffnen.",
             "tables": "Relevant sind Tabellen dieses Anhangs: ihre bisherigen Zusammenfassungen gelten weiter."}


def _wording(card):
    parts = []
    if card.get("old_text"):
        css = "context" if card.get("old_is_context") else "old"
        label = "Bisher (mit umgebendem Text)" if card.get("old_is_context") else "Bisher"
        parts.append(f"<div>{label}:</div><div class='{css}'>{html.escape(card['old_text'][:6000])}</div>")
    if card.get("new_text"):
        parts.append(f"<div>Neu:</div><div class='new'>{html.escape(card['new_text'][:6000])}</div>")
    links = [f"<a href='{html.escape(card[key])}'>{label}</a>" for key, label in
             (("new_link", "Stelle auf EUR-Lex"), ("changing_act_link", "Ändernder Rechtsakt")) if card.get(key)]
    return f"<div class='card'><b>{html.escape(card['line'])}</b>{''.join(parts)}<div>{' · '.join(links)}</div></div>"


def unit_html(record):
    summary, changed = record.get("summary") or {}, record.get("what_changed") or {}
    today = record.get("today") or {}
    out = [f"<div class='unit'><h2>{html.escape(record['name'])}</h2>",
           f"<div class='meta'>Relevant: {html.escape(', '.join(p['locator'] for p in record['provisions'] + record['tables']))}"
           + (f" · Stand {html.escape(day_de(today['version_date']))} <a href='{html.escape(today['link'])}'>EUR-Lex</a>"
              if today.get("version_date") and today.get("link") else "") + "</div>"]
    if summary:
        out.append(f"<p><b>{html.escape(summary.get('headline', ''))}</b><br>{html.escape(summary.get('overview', ''))}</p>")
        out.append("<ul>" + "".join(f"<li>{html.escape(b['text'])}</li>" for b in summary.get("bullets", [])) + "</ul>")
    elif record.get("kind") in KIND_NOTE:
        out.append(f"<p class='meta'>{KIND_NOTE[record['kind']]}</p>")
    if changed.get("facts") or changed.get("sentence"):
        out.append("<div class='changed'><b>Was hat sich geändert</b><br>"
                   + "<br>".join(html.escape(fact) for fact in changed.get("facts", []))
                   + (f"<br><i>{html.escape(changed['sentence'])}</i>" if changed.get("sentence") else "")
                   + (f"<div class='check'>Zahl prüfen: {html.escape(str(changed['number_check']))}</div>"
                      if changed.get("number_check") else "") + "</div>")
    out += [f"<div class='upcoming'>{html.escape(line)}</div>" for line in record.get("upcoming", [])]
    cards = record.get("changes", {})
    out += [_wording(card) for card in cards.get("upcoming", []) + cards.get("latest", [])]
    earlier = cards.get("history", []) + cards.get("no date", [])
    if earlier:
        out.append(f"<details><summary>Frühere Änderungen ({len(earlier)})</summary>"
                   + "".join(f"<div>{html.escape(card['line'])} · <a href='{html.escape(card.get('changing_act_link', ''))}'>"
                             "EUR-Lex</a></div>" for card in earlier) + "</details>")
    if record.get("failure"):
        out.append(f"<div class='check'>Fehler: {html.escape(record['failure'])}</div>")
    return "".join(out) + "</div>"


def preview(out, records):
    folder = out / "preview"
    folder.mkdir(parents=True, exist_ok=True)
    acts = defaultdict(list)
    for record in records:
        acts[record["celex"]].append(record)
    links = []
    for celex, items in sorted(acts.items()):
        items.sort(key=lambda r: base.locator_key(r["name"]))
        page = (f"<!DOCTYPE html><html lang='de'><meta charset='utf-8'><title>{html.escape(celex)}</title>"
                f"<style>{STYLE}</style><p><a href='index.html'>← Übersicht</a></p>"
                f"<h1>{html.escape(items[0]['title'] or celex)}</h1>"
                f"<div class='meta'>{html.escape(celex)} · Heute geltender Text: {html.escape(version_de(items[0].get('today_version')))}"
                f"</div>{''.join(unit_html(r) for r in items)}"
                f"<p class='meta'>{html.escape(base.NOTE['de'])}</p></html>")
        name = f"{safe_name(celex)}.html"
        (folder / name).write_text(page, encoding="utf-8")
        links.append(f"<li><a href='{name}'>{html.escape(celex)}</a> {html.escape(items[0]['title'][:140])} "
                     f"({len(items)})</li>")
    (folder / "index.html").write_text(
        f"<!DOCTYPE html><html lang='de'><meta charset='utf-8'><title>Geänderte Vorschriften</title>"
        f"<style>{STYLE}</style><h1>Geänderte relevante Vorschriften ({len(records)})</h1><ul>{''.join(links)}</ul></html>",
        encoding="utf-8")


# ------------------------------------------------------------------ run

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--changed-dir", type=Path, required=True,
                        help="Folder written by the backend: reports/changed-texts/<time>")
    parser.add_argument("--classification-dir", type=Path, default=DEFAULT_CLASSIFICATION)
    parser.add_argument("--extra-classification-dir", type=Path, nargs="*", default=[],
                        help="More classifications, for example of the articles inserted later "
                             "(screen_new_articles.py). Their provisions are added to the act.")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--levels", nargs="+", choices=KEPT, default=list(KEPT))
    parser.add_argument("--language", choices=tuple(base.LANGUAGES), default="de")
    parser.add_argument("--sample", type=int, help="Only this many articles or annexes (the first ones).")
    parser.add_argument("--celex", nargs="+", default=[], help="Only these acts.")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--count-only", action="store_true")
    args = parser.parse_args(argv)
    base.OUTPUT_LANGUAGE = args.language
    out = args.output_dir.resolve()
    try:
        systems = {name: base.system_prompt(name, args.language) for name in ("provision", "merge")}
        systems["change"] = CHANGE_PROMPT.read_text(encoding="utf-8").replace(
            "<LANGUAGE>", base.LANGUAGES[args.language]).strip() + "\n"
        classified = merged([args.classification_dir, *args.extra_classification_dir])
        units, facts = plan(args.changed_dir, classified, set(args.levels), set(args.celex))
    except (OSError, ValueError, KeyError) as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 1
    if args.sample:
        units = units[:args.sample]
    kinds = Counter(unit_kind(unit) for unit in units)
    print(f"Changed articles and annexes with a relevant provision: {len(units):,} in "
          f"{len({u['celex'] for u in units}):,} acts.", flush=True)
    print("  " + ", ".join(f"{key} {value:,}" for key, value in sorted(facts.items())), flush=True)
    print("  " + ", ".join(f"{key} {value:,}" for key, value in sorted(kinds.items())), flush=True)
    if args.count_only or not units:
        return 0
    write_json(out / "run_config.json", {"version": VERSION, "language": args.language,
                                         "changed_dir": str(args.changed_dir.resolve()),
                                         "classification": str(args.classification_dir),
                                         "system_prompts": systems})
    client = QwenClient.from_env(args.env_file, retries=1)
    workers = max(1, min(args.workers, MAX_WORKERS))

    def open_units():
        todo = [unit for unit in units if saved_record(out, unit) is None]
        return todo, len(units) - len(todo)

    def worker(progress):
        def work(unit):
            failed = False
            try:
                record = summarise_unit(client, systems, unit)
            except ConnectionLost:
                raise
            except Exception as exc:  # noqa: BLE001 - one unreadable article must not stop the run
                record = {key: unit[key] for key in ("celex", "title", "key", "provisions", "tables")}
                record.update(version=VERSION, name=unit["root"]["name"], kind=unit_kind(unit),
                              failure=f"{type(exc).__name__}: {exc}"[:400])
                failed = True
                print(f"Could not summarise {unit['celex']} {unit['root']['name']}: {record['failure']}", flush=True)
            write_json(unit_path(out, unit), record)
            progress.step(failed)
        return work

    try:
        ok, error = reachable(client)
        if not ok:
            wait_until_reachable(client, error)
        run_until_done(client, "Changed", open_units, worker, workers, after_pass=lambda: export(out, units))
    except KeyboardInterrupt:
        export(out, units)
        print("\nStopped by Ctrl+C. Run the same command to continue.", flush=True)
        return 130
    summary = export(out, units)
    print(f"\nFinished: {summary}")
    print(f"Open in a browser: {out / 'preview' / 'index.html'}\nTo check by hand: {out / 'review.csv'}\n"
          f"For the database import: {out / 'changed_provisions.json'}")
    if summary.get("failed"):
        print("Run the same command again to retry what failed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
