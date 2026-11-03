#!/usr/bin/env python3
"""Export the EUR-Lex acts of the Legal Radar database to JSON files (READ-ONLY).

Which acts:
    Every EUR-Lex Regulation, Directive and Decision (CELEX 3yyyyR/L/Dnnnn) that the
    backend's primary filter has looked at - kept AND blocked. Blocked acts only have
    their titles in the database (no notice, no text); they are exported too, so we can
    check later how many relevant acts the blocklist drops.
    Not exported: corrigenda, consolidated texts, national measures, case law, and
    CELEX numbers that only appear as link targets (never evaluated).

What it writes (folder data/eurlex/):
    acts.jsonl        one line per act: titles, dates, act type, filter decision,
                      EuroVoc, subject matter, directory codes, addressees, legal basis,
                      amends/repeals, text statistics
    text_en.jsonl     one line per act with English text: recitals, outline,
                      articles (full text), annexes (shortened), unstructured text
    text_de.jsonl     the same in German
    export_info.json  counts, options, coverage, timings
and reports/eurlex_export_summary.md (short readable summary - send me this one).

It changes nothing in the database: one read-only transaction, nothing else.

Usage (in the AI experiments folder):
    py tools\\export_eurlex.py --check          counts only, about 1 minute, writes no data
    py tools\\export_eurlex.py --limit 200      quick test with 200 acts
    py tools\\export_eurlex.py                  full export
Options: --env FILE (take DATABASE_URL from another .env, e.g. the backend's),
         --no-text, --no-notices, --celex 32011R0305,32024R3110, --annex-max-chars 50000
Needs:   py -3 -m pip install "psycopg[binary]"
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_VERSION = 'eurlex-export-1.0.0'

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    sys.exit('psycopg is missing. Run:  py -3 -m pip install "psycopg[binary]"')

ACT_CELEX = re.compile(r'^3\d{4}[RLD]\d{4}(\(\d{2}\))?$')
CORRIGENDUM_CELEX = re.compile(r'R\(\d+\)$')
ACT_TYPES = {'R': 'regulation', 'L': 'directive', 'D': 'decision'}
CONTAINERS = ('TITLE', 'PART', 'CHAPTER', 'SECTION', 'SUBSECTION')
TEXT_TYPES = CONTAINERS + ('ARTICLE', 'ANNEX', 'APPENDIX', 'DOCUMENT_TEXT')
# Act-to-act links that change another act (family "OPERATIONAL" in the backend).
CHANGE_TYPES = ('AMENDS', 'REPEALS', 'IMPLICITLY_REPEALS', 'REPLACES', 'COMPLETES', 'ADDS_TO',
                'IMPLEMENTS', 'DEROGATES', 'SUSPENDS', 'PARTIALLY_SUSPENDS', 'EXTENDS_APPLICATION',
                'EXTENDS_VALIDITY', 'DEFERS_APPLICATION', 'INCORPORATES', 'REESTABLISHES',
                'RENDERS_OBSOLETE', 'CONFIRMS')
QUALITY_RANK = {'VALIDATED': 4, 'DEGRADED': 3, 'LEGACY_UNVALIDATED': 2, 'REJECTED': 0}
MAX_CHANGES, MAX_CHANGED_BY, MAX_BASIS_FOR = 50, 20, 10
MAX_ARTICLE_CHARS = 100_000
MAX_DOCUMENT_TEXT_CHARS = 300_000


# ----------------------------------------------------------------------------- setup
def read_env_value(path, key):
    try:
        for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line.startswith(key + '='):
                return line.split('=', 1)[1].strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def database_url(env_file):
    """--env file > DATABASE_URL variable > .env here > backend .env next to this folder."""
    tried = []
    if env_file:
        url = read_env_value(env_file, 'DATABASE_URL')
        if not url:
            sys.exit(f'DATABASE_URL not found in {env_file}')
        return url, str(env_file)
    if os.getenv('DATABASE_URL'):
        return os.environ['DATABASE_URL'], 'environment variable DATABASE_URL'
    for path in (ROOT / '.env', ROOT.parent / 'legal_radar_backend' / '.env'):
        url = read_env_value(path, 'DATABASE_URL')
        tried.append(str(path))
        if url and ':change_me@' not in url:
            return url, str(path)
    sys.exit('No usable DATABASE_URL found (looked in: ' + ', '.join(tried) + ').\n'
             'Copy the DATABASE_URL line from the backend .env into the .env of this folder,\n'
             'or run with  --env "D:\\path\\to\\legal_radar_backend\\.env"')


def masked(url):
    return re.sub(r'(://[^:/@]+):[^@]*@', r'\1:***@', url)


def connect(url):
    url = re.sub(r'^postgres(ql)?\+\w+://', 'postgresql://', url)   # SQLAlchemy style -> plain
    try:
        conn = psycopg.connect(url, autocommit=False)
    except psycopg.OperationalError as exc:
        text = str(exc)
        hint = ''
        if 'password authentication failed' in text:
            hint = '\nThe password is wrong: use the DATABASE_URL from the backend .env (or --env).'
        elif 'Connection refused' in text or 'could not connect' in text or 'timeout' in text.lower():
            hint = '\nIs PostgreSQL running? Start it with scripts\\start_postgres_windows.ps1'
        sys.exit(f'Could not connect: {text.strip()}{hint}')
    conn.read_only = True                                        # nothing can be written
    conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ  # one consistent snapshot
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = '20min'")
    return conn


def rows(conn, sql, params=None, binary=False):
    with conn.cursor(binary=binary) as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def iso(value):
    if value is None:
        return None
    return value.isoformat() if hasattr(value, 'isoformat') else str(value)


# ----------------------------------------------------------------------------- notices
def _strip_namespaces(root):
    for node in root.iter():
        if isinstance(node.tag, str) and '}' in node.tag:
            node.tag = node.tag.rsplit('}', 1)[1]


def _concept(node):
    ident = (node.findtext('IDENTIFIER') or node.findtext('OP-CODE')
             or node.findtext('URI/IDENTIFIER') or '').strip()
    label = (node.findtext('PREFLABEL') or '').strip()
    return {'id': ident, 'label': label} if ident or label else None


def _add(target, item):
    if item and item['id'] not in {x['id'] for x in target}:
        target.append(item)


def treaty_label(celex, detail):
    m = re.match(r'^1(\d{4})([EMA])(\d+)([A-Z]?)$', celex or '')
    if not m:
        return None
    year, kind, number, suffix = int(m.group(1)), m.group(2), int(m.group(3)), m.group(4)
    treaty = {'E': 'TFEU' if year >= 2008 else 'EC Treaty', 'M': 'TEU', 'A': 'Euratom Treaty'}[kind]
    text = f'{treaty} Art. {number}{suffix.lower()}'
    if detail.get('PARAGRAPH'):
        text += f'({detail["PARAGRAPH"]})'
    return text


def parse_notice(data):
    """Read EuroVoc, subject matter, directory codes, addressees, authors, DG, resource type,
    legal basis and transposition dates from a CELLAR branch/object notice (XML bytes)."""
    head = bytes(data[:4096]).upper()
    if b'<!DOCTYPE' in head or b'<!ENTITY' in bytes(data).upper():
        raise ValueError('DTD/entity declarations are not accepted')
    root = ET.fromstring(bytes(data))
    _strip_namespaces(root)
    work = root.find('WORK') if root.tag == 'NOTICE' else None
    out = {k: [] for k in ('eurovoc', 'eurovoc_microthesauri', 'eurovoc_domains', 'directory_codes',
                           'subject_matters', 'addressees', 'authors', 'responsible_dg',
                           'resource_type', 'legal_basis', 'transposition_dates')}
    out['concept_tags'] = collections.Counter()
    if work is None:
        return out
    for field in work:
        tag, kind = field.tag, field.get('type')
        if kind in ('concept', 'concept_facet', 'concept_level'):
            out['concept_tags'][tag] += 1
        if tag == 'WORK_IS_ABOUT_CONCEPT_EUROVOC':
            if kind == 'concept':
                _add(out['eurovoc'], _concept(field))
            for child in field:
                if child.tag.endswith('_CONCEPT'):
                    _add(out['eurovoc'], _concept(child))
                elif child.tag.endswith('_MTH'):
                    _add(out['eurovoc_microthesauri'], _concept(child))
                elif child.tag.endswith('_DOM'):
                    _add(out['eurovoc_domains'], _concept(child))
        elif tag == 'RESOURCE_LEGAL_IS_ABOUT_CONCEPT_DIRECTORY-CODE':
            levels = [(int(m.group(1)), c) for c in field
                      for m in [re.search(r'_(\d+)$', c.tag)] if m] if kind != 'concept' else [(1, field)]
            path = [_concept(c) for _, c in sorted(levels, key=lambda x: x[0])]
            path = [p for p in path if p]
            if path:
                _add(out['directory_codes'], {'id': path[-1]['id'], 'label': ' > '.join(p['label'] for p in path)})
        elif tag == 'RESOURCE_LEGAL_IS_ABOUT_SUBJECT-MATTER':
            for child in ([field] if kind == 'concept' else list(field)):
                _add(out['subject_matters'], _concept(child))
        elif tag == 'ADDRESSES' or tag.startswith('RESOURCE_LEGAL_ADDRESSES'):
            _add(out['addressees'], _concept(field))
        elif tag in ('WORK_CREATED_BY_AGENT', 'CREATED_BY'):
            _add(out['authors'], _concept(field))
        elif tag == 'RESOURCE_LEGAL_RESPONSIBILITY_OF_AGENT':
            _add(out['responsible_dg'], _concept(field))
        elif tag == 'WORK_HAS_RESOURCE-TYPE':
            _add(out['resource_type'], _concept(field))
        elif tag == 'RESOURCE_LEGAL_BASED_ON_RESOURCE_LEGAL':
            celex = None
            for uri in field.iter('URI'):
                if (uri.findtext('TYPE') or '').lower() == 'celex':
                    celex = (uri.findtext('IDENTIFIER') or '').strip()
                    break
            detail = {}
            annotation = field.find('ANNOTATION')
            if annotation is not None:
                for child in annotation:
                    if child.tag != 'COMMENT_ON_LEGAL_BASIS' and (child.text or '').strip():
                        detail[child.tag] = child.text.strip()
            if celex and celex not in {x['celex'] for x in out['legal_basis']}:
                out['legal_basis'].append({'celex': celex, 'detail': detail,
                                           'label': treaty_label(celex, detail)})
        elif tag == 'DIRECTIVE_DATE_TRANSPOSITION':
            value = (field.findtext('VALUE') or '')[:10]
            if value and value not in out['transposition_dates']:
                out['transposition_dates'].append(value)
    return out


def merge_labels(en_list, de_list):
    """[{'id','label'}] in EN and DE -> [{'id','en','de'}] keeping EN order."""
    de = {x['id']: x['label'] for x in de_list}
    merged = [{'id': x['id'], 'en': x['label'], 'de': de.get(x['id'])} for x in en_list]
    known = {x['id'] for x in en_list}
    merged += [{'id': x['id'], 'en': None, 'de': x['label']} for x in de_list if x['id'] not in known]
    return merged


# ----------------------------------------------------------------------------- text
def choose_versions(version_rows):
    """Per language: the version whose text we export, and the one we take recitals from."""
    by_lang = collections.defaultdict(list)
    for v in version_rows:
        by_lang[v['language']].append(v)
    chosen = {}
    for lang, items in by_lang.items():
        def rank(v):
            return (v['is_preferred'], v['is_current'], QUALITY_RANK.get(v['quality_status'], 1),
                    v['version_kind'] == 'CONSOLIDATED', iso(v['version_date']) or '', iso(v['fetched_at']) or '')
        text = max(items, key=rank)
        recital = text if text['recital_count'] else max(
            items, key=lambda v: (v['recital_count'], v['version_kind'] == 'ORIGINAL', iso(v['fetched_at']) or ''))
        chosen[lang] = (text, recital if recital['recital_count'] else None)
    return chosen


def build_text(elements, annex_max):
    by_id = {e['id']: e for e in elements}

    def context(e):
        labels, in_annex, seen = [], False, set()
        parent = by_id.get(e['parent_id'])
        while parent is not None and parent['id'] not in seen:
            seen.add(parent['id'])
            if parent['element_type'] in ('ANNEX', 'APPENDIX'):
                in_annex = True
            elif parent['element_type'] in CONTAINERS:
                labels.append(parent['heading'] or parent['identifier'])
            parent = by_id.get(parent['parent_id'])
        return ' > '.join(reversed(labels)) or None, in_annex

    outline, articles, annexes, document_text = [], [], [], []
    for e in elements:
        kind = e['element_type']
        if kind in CONTAINERS:
            outline.append({'type': kind, 'number': e['number'], 'heading': e['heading'] or e['identifier']})
        elif kind == 'ARTICLE':
            within, in_annex = context(e)
            if in_annex:
                continue
            text = e['body_text'] or ''
            item = {'n': e['number'], 'heading': e['heading'] or e['identifier'], 'within': within, 'text': text[:MAX_ARTICLE_CHARS]}
            if len(text) > MAX_ARTICLE_CHARS:
                item.update(truncated=True, chars=len(text))
            articles.append(item)
        elif kind in ('ANNEX', 'APPENDIX'):
            if kind == 'APPENDIX' and context(e)[1]:
                continue
            text = e['body_text'] or ''
            cut = annex_max and len(text) > annex_max
            annexes.append({'n': e['number'], 'type': kind, 'heading': e['heading'] or e['identifier'],
                            'text': text[:annex_max] if cut else text, 'chars': len(text), 'truncated': bool(cut)})
        elif kind == 'DOCUMENT_TEXT':
            document_text.append(e['body_text'] or '')
    joined = '\n\n'.join(t for t in document_text if t)
    return outline, articles, annexes, (joined[:MAX_DOCUMENT_TEXT_CHARS] or None)


def recitals_from(json_value, combined):
    items = []
    for r in json_value or []:
        if isinstance(r, dict) and (r.get('text') or '').strip():
            items.append({'n': r.get('number'), 'text': r['text']})
    if not items and combined:
        for part in combined.split('\n\n'):
            m = re.match(r'^\((\d+[a-zA-Z]?)\)\s*(.*)$', part.strip(), re.S)
            items.append({'n': m.group(1), 'text': m.group(2)} if m else {'n': None, 'text': part.strip()})
    return items


# ----------------------------------------------------------------------------- queries
SELECT_DOCS = """
    SELECT sd.id::text AS id, sd.external_id AS celex, sd.document_kind, sd.resource_type,
           sd.title_en, sd.title_de, sd.canonical_title, sd.publication_date, sd.document_date,
           sd.in_force, sd.lifecycle_status, sd.scope_status, sd.date_entry_into_force,
           sd.date_application, sd.date_end_validity, sd.eli_uri, sd.information_url,
           sd.metadata_json -> 'titles' AS meta_titles,
           li.id::text AS instrument_id, li.act_kind, li.lifecycle_status AS instrument_lifecycle
    FROM source_documents sd
    LEFT JOIN legal_instruments li ON li.source_document_id = sd.id
    WHERE sd.source = 'EUR_LEX' AND sd.deleted_at IS NULL AND sd.external_id LIKE '3%%'
"""
SELECT_DECISIONS = """
    SELECT DISTINCT ON (rd.source_document_id)
           rd.source_document_id::text AS id, rd.primary_decision, rd.decision_stage,
           rd.decision_reason, rd.filter_evidence, rd.filter_signals, rd.relationship_protected,
           rd.discovery_role, rd.fetch_status, rd.processed_at, mr.run_key, mr.mode
    FROM run_documents rd
    JOIN monitor_runs mr ON mr.id = rd.run_id
    JOIN source_documents sd ON sd.id = rd.source_document_id
    WHERE sd.source = 'EUR_LEX' AND sd.external_id LIKE '3%%'
    ORDER BY rd.source_document_id, rd.processed_at DESC
"""


def dict_rows(conn, sql, params=None, binary=False):
    with conn.cursor(binary=binary, row_factory=dict_row) as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def load_population(conn, args, info):
    docs = dict_rows(conn, SELECT_DOCS)
    decisions = {d['id']: d for d in dict_rows(conn, SELECT_DECISIONS)}
    wanted = {c.strip().upper() for c in (args.celex or '').split(',') if c.strip()}
    skipped = collections.Counter()
    acts = []
    for d in docs:
        celex = d['celex'].upper()
        if wanted and celex not in wanted:
            continue
        if CORRIGENDUM_CELEX.search(celex) or d['document_kind'] == 'CORRIGENDUM':
            skipped['corrigendum'] += 1
        elif not ACT_CELEX.match(celex) or d['document_kind'] == 'CONSOLIDATED':
            skipped['not a regulation/directive/decision'] += 1
        elif d['id'] not in decisions and not d['instrument_id']:
            skipped['never evaluated (only a link target)'] += 1
        elif getattr(args, 'kept_only', False) and (decisions.get(d['id']) or {}).get('primary_decision') != 'KEEP':
            skipped['latest decision is not KEEP'] += 1
        else:
            d['decision'] = decisions.get(d['id'])
            acts.append(d)
    acts.sort(key=lambda d: d['celex'])
    titles = {d['celex'].upper(): (d['title_en'], d['title_de']) for d in docs}
    info['skipped'] = dict(skipped)
    if args.limit and len(acts) > args.limit:
        step = len(acts) / args.limit          # spread the test over all years and act types
        acts = [acts[int(i * step)] for i in range(args.limit)]
    return acts, titles


def profile(conn, acts, info):
    """Counts that show what is in the database (also used by --check)."""
    counts = {
        'acts': len(acts),
        'decision': collections.Counter((a['decision'] or {}).get('primary_decision', 'NO_DECISION') for a in acts),
        'stage': collections.Counter((a['decision'] or {}).get('decision_stage', 'NO_DECISION') for a in acts),
        'act_type': collections.Counter(ACT_TYPES[a['celex'][5]] for a in acts),
        'in_force': collections.Counter(str(a['in_force']) for a in acts),
        'with_instrument': sum(1 for a in acts if a['instrument_id']),
        'title_en': sum(1 for a in acts if a['title_en']),
        'title_de': sum(1 for a in acts if a['title_de']),
    }
    ids = [a['instrument_id'] for a in acts if a['instrument_id']]
    langs = collections.Counter()
    for chunk in chunks(ids, 5000):
        for lang, n in rows(conn, """SELECT language, count(DISTINCT instrument_id) FROM instrument_versions
                                     WHERE instrument_id = ANY(%s::uuid[]) GROUP BY language""", (chunk,)):
            langs[lang] += n
    counts['acts_with_text'] = dict(langs)
    doc_ids = [a['id'] for a in acts]
    notices = collections.Counter()
    for chunk in chunks(doc_ids, 5000):
        for kind, n in rows(conn, """SELECT snapshot_kind, count(DISTINCT source_document_id) FROM source_snapshots
                                     WHERE source_document_id = ANY(%s::uuid[])
                                       AND snapshot_kind IN ('NOTICE_EN', 'NOTICE_DE', 'NOTICE_OBJECT')
                                     GROUP BY snapshot_kind""", (chunk,)):
            notices[kind] += n
    counts['acts_with_notice'] = dict(notices)
    sizes = {}
    for table in ('legal_elements', 'source_snapshots', 'instrument_versions', 'source_documents'):
        sizes[table] = rows(conn, 'SELECT pg_size_pretty(pg_total_relation_size(%s::regclass))', (table,))[0][0]
    counts['table_sizes'] = sizes
    info['population'] = {k: (dict(v) if isinstance(v, collections.Counter) else v) for k, v in counts.items()}
    return counts


def chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


# ----------------------------------------------------------------------------- batch export
def export_batch(conn, batch, titles, args, stats, writers):
    doc_ids = [a['id'] for a in batch]
    inst_ids = [a['instrument_id'] for a in batch if a['instrument_id']]

    # --- relations (only kept acts have them in the database)
    relations = collections.defaultdict(lambda: {'changes': [], 'changed_by': [], 'legal_basis': [], 'basis_for': []})
    types = list(CHANGE_TYPES) + ['LEGAL_BASIS']
    for r in dict_rows(conn, """
            SELECT r.source_document_id::text AS src, r.target_document_id::text AS tgt, r.relation_type AS rel,
                   s.external_id AS src_celex, t.external_id AS tgt_celex
            FROM instrument_relationships r
            JOIN source_documents s ON s.id = r.source_document_id
            JOIN source_documents t ON t.id = r.target_document_id
            WHERE r.is_direct AND r.relation_type = ANY(%s)
              AND (r.source_document_id = ANY(%s::uuid[]) OR r.target_document_id = ANY(%s::uuid[]))""",
            (types, doc_ids, doc_ids)):
        if r['rel'] == 'LEGAL_BASIS':
            relations[r['src']]['legal_basis'].append(r['tgt_celex'])
            relations[r['tgt']]['basis_for'].append(r['src_celex'])
        else:
            relations[r['src']]['changes'].append((r['rel'], r['tgt_celex']))
            relations[r['tgt']]['changed_by'].append((r['rel'], r['src_celex']))

    # --- notices: pick the newest snapshot id per act and kind, then read payloads in small groups
    notice_info = {}
    if not args.no_notices:
        newest = {}
        for sid, doc, kind, fetched in rows(conn, """
                SELECT id::text, source_document_id::text, snapshot_kind, fetched_at FROM source_snapshots
                WHERE source_document_id = ANY(%s::uuid[]) AND snapshot_kind IN ('NOTICE_EN', 'NOTICE_DE', 'NOTICE_OBJECT')""",
                (doc_ids,)):
            if (doc, kind) not in newest or fetched > newest[(doc, kind)][1]:
                newest[(doc, kind)] = (sid, fetched)
        wanted = {}
        for (doc, kind), (sid, _) in newest.items():
            if kind == 'NOTICE_OBJECT' and (doc, 'NOTICE_EN') in newest:
                continue
            wanted[sid] = (doc, 'DE' if kind == 'NOTICE_DE' else 'EN')
        parsed = collections.defaultdict(dict)
        for group in chunks(list(wanted), 10):
            for sid, payload in rows(conn, 'SELECT id::text, payload FROM source_snapshots WHERE id = ANY(%s::uuid[])',
                                     (group,), binary=True):
                doc, lang = wanted[sid]
                try:
                    parsed[doc][lang] = parse_notice(payload)
                except Exception as exc:  # noqa: BLE001 - one bad notice must not stop the export
                    stats['notice_errors'] += 1
                    if len(stats['notice_error_examples']) < 5:
                        stats['notice_error_examples'].append(f'{doc}: {str(exc)[:200]}')
        for doc, by_lang in parsed.items():
            en, de = by_lang.get('EN'), by_lang.get('DE')
            base = en or de
            for source in (en, de):
                if source:
                    stats['concept_tags'].update(source['concept_tags'])
            item = {key: merge_labels((en or {}).get(key, []), (de or {}).get(key, []))
                    for key in ('eurovoc', 'eurovoc_microthesauri', 'eurovoc_domains', 'directory_codes',
                                'subject_matters', 'addressees', 'authors', 'responsible_dg', 'resource_type')}
            item['legal_basis'] = base['legal_basis']
            item['transposition_dates'] = base['transposition_dates']
            item['languages'] = sorted(by_lang)
            notice_info[doc] = item

    # --- text versions
    versions = collections.defaultdict(list)
    if inst_ids and not args.no_text:
        for v in dict_rows(conn, """
                SELECT iv.instrument_id::text AS instrument_id, iv.id::text AS id, iv.language, iv.version_key,
                       iv.version_kind, iv.version_date, iv.is_current, iv.is_preferred, iv.quality_status,
                       iv.quality_score, iv.source_format, iv.fetched_at, coalesce(rs.recital_count, 0) AS recital_count
                FROM instrument_versions iv LEFT JOIN recital_sets rs ON rs.version_id = iv.id
                WHERE iv.instrument_id = ANY(%s::uuid[]) AND iv.language IN ('EN', 'DE')""", (inst_ids,)):
            versions[v['instrument_id']].append(v)
    chosen = {inst: choose_versions(vs) for inst, vs in versions.items()}
    text_ids = [t['id'] for c in chosen.values() for t, _ in c.values()]
    recital_ids = [r['id'] for c in chosen.values() for _, r in c.values() if r]
    elements = collections.defaultdict(list)
    for group in chunks(text_ids, 40):
        for e in dict_rows(conn, """
                SELECT version_id::text AS version_id, id::text AS id, parent_id::text AS parent_id, element_type,
                       number, identifier, heading, ordinal, body_text
                FROM legal_elements WHERE version_id = ANY(%s::uuid[]) AND element_type = ANY(%s)
                ORDER BY version_id, ordinal""", (group, list(TEXT_TYPES))):
            elements[e['version_id']].append(e)
    recital_sets = {}
    for group in chunks(recital_ids, 100):
        for vid, rjson, combined in rows(conn, """SELECT version_id::text, recitals_json, combined_text
                                                FROM recital_sets WHERE version_id = ANY(%s::uuid[])""", (group,)):
            recital_sets[vid] = recitals_from(rjson, combined)

    # --- write one line per act (+ text lines)
    for a in batch:
        decision = a['decision'] or {}
        rel = relations.get(a['id'], {'changes': [], 'changed_by': [], 'legal_basis': [], 'basis_for': []})
        notice = notice_info.get(a['id'], {})
        meta_titles = a['meta_titles'] if isinstance(a['meta_titles'], dict) else {}
        title_en = a['title_en'] or meta_titles.get('EN')
        title_de = a['title_de'] or meta_titles.get('DE')
        changes = sorted(set(rel['changes']), key=lambda x: x[1], reverse=True)
        changed_by = sorted(set(rel['changed_by']), key=lambda x: x[1], reverse=True)
        basis_for = sorted(set(rel['basis_for']), reverse=True)
        role = ('BOTH' if changes and changed_by else 'CHILD' if changes else 'BASE' if changed_by else 'STANDALONE')

        legal_basis = notice.get('legal_basis')
        if not legal_basis:
            legal_basis = [{'celex': c, 'detail': {}, 'label': treaty_label(c, {})} for c in sorted(set(rel['legal_basis']))]
        for item in legal_basis:
            t = titles.get(item['celex'].upper())
            if not item['label'] and t:
                item['title_en'], item['title_de'] = t

        text_summary = {}
        chosen_for_act = chosen.get(a['instrument_id'], {})
        for lang in ('EN', 'DE'):
            if lang not in chosen_for_act:
                continue
            text_v, recital_v = chosen_for_act[lang]
            outline, articles, annexes, document_text = build_text(elements.get(text_v['id'], []), args.annex_max_chars)
            recitals = recital_sets.get(recital_v['id'], []) if recital_v else []
            chars = (sum(len(x['text']) for x in recitals) + sum(len(x['text']) for x in articles)
                     + sum(len(x['text']) for x in annexes) + len(document_text or ''))
            version = {'key': text_v['version_key'], 'kind': text_v['version_kind'], 'date': iso(text_v['version_date']),
                       'is_preferred': text_v['is_preferred'], 'quality': text_v['quality_status'],
                       'quality_score': text_v['quality_score'], 'format': text_v['source_format']}
            if lang not in writers:
                continue
            writers[lang].write(json.dumps({
                'celex': a['celex'], 'language': lang, 'version': version,
                'recitals_version': None if not recital_v or recital_v['id'] == text_v['id'] else recital_v['version_key'],
                'recitals': recitals, 'outline': outline, 'articles': articles, 'annexes': annexes,
                'document_text': document_text}, ensure_ascii=False) + '\n')
            text_summary[lang.lower()] = {'version_kind': text_v['version_kind'], 'version_date': iso(text_v['version_date']),
                                          'quality': text_v['quality_status'], 'recitals': len(recitals),
                                          'articles': len(articles), 'annexes': len(annexes),
                                          'unstructured': bool(document_text), 'chars': chars}
            stats['text_' + lang.lower()] += 1
            stats['recitals_' + lang.lower()] += bool(recitals)

        record = {
            'id': a['id'],
            'celex': a['celex'],
            'act_type': ACT_TYPES[a['celex'][5]],
            'document_kind': a['document_kind'] or a['act_kind'],
            'resource_type': a['resource_type'],
            'resource_type_label': notice.get('resource_type', []),
            'title': {'en': title_en, 'de': title_de},
            'dates': {'document': iso(a['document_date']), 'publication': iso(a['publication_date']),
                      'entry_into_force': iso(a['date_entry_into_force']), 'application': iso(a['date_application']),
                      'end_of_validity': iso(a['date_end_validity']),
                      'transposition': notice.get('transposition_dates', [])},
            'in_force': a['in_force'],
            'lifecycle_status': a['lifecycle_status'] or a['instrument_lifecycle'],
            'scope_status': a['scope_status'],
            'filter': {'decision': decision.get('primary_decision'), 'stage': decision.get('decision_stage'),
                       'reason': decision.get('decision_reason'),
                       'evidence': [{k: e.get(k) for k in ('kind', 'rule_id', 'language', 'matched_text')}
                                    for e in (decision.get('filter_evidence') or []) if isinstance(e, dict)],
                       'signals': [{k: e.get(k) for k in ('rule_id', 'language', 'matched_text')}
                                   for e in (decision.get('filter_signals') or []) if isinstance(e, dict)],
                       'relationship_protected': decision.get('relationship_protected'),
                       'discovery_role': decision.get('discovery_role'), 'run': decision.get('run_key'),
                       'at': iso(decision.get('processed_at'))},
            'eurovoc': notice.get('eurovoc', []),
            'eurovoc_microthesauri': notice.get('eurovoc_microthesauri', []),
            'eurovoc_domains': notice.get('eurovoc_domains', []),
            'subject_matters': notice.get('subject_matters', []),
            'directory_codes': notice.get('directory_codes', []),
            'addressees': notice.get('addressees', []),
            'authors': notice.get('authors', []),
            'responsible_dg': notice.get('responsible_dg', []),
            'legal_basis': legal_basis,
            'family': {'role': role,
                       'changes': [{'relation': r, 'celex': c, 'title_en': (titles.get(c.upper()) or (None, None))[0],
                                    'title_de': (titles.get(c.upper()) or (None, None))[1]} for r, c in changes[:MAX_CHANGES]],
                       'changes_count': len(changes),
                       'changed_by': [{'relation': r, 'celex': c} for r, c in changed_by[:MAX_CHANGED_BY]],
                       'changed_by_count': len(changed_by),
                       'basis_for': basis_for[:MAX_BASIS_FOR], 'basis_for_count': len(basis_for)},
            'text': text_summary,
            'notice_languages': notice.get('languages', []),
            'links': {'eur_lex': a['information_url'] or
                      f'https://eur-lex.europa.eu/legal-content/EN/ALL/?uri=CELEX:{a["celex"]}', 'eli': a['eli_uri']},
        }
        writers['acts'].write(json.dumps(record, ensure_ascii=False) + '\n')
        stats['acts'] += 1
        stats['notice'] += bool(notice)
        stats['eurovoc'] += bool(record['eurovoc'])
        stats['eurovoc_counter'].update(d['en'] or d['de'] or d['id'] for d in record['eurovoc_domains'])


# ----------------------------------------------------------------------------- main
def write_summary(path, info, stats):
    pop = info['population']
    lines = [f'# EUR-Lex export summary ({info["finished"][:16]})', '',
             f'- Script: {SCRIPT_VERSION}; database `{info["database"]}` (alembic {info["alembic"]})',
             f'- Acts exported: **{stats["acts"]:,}** in {info["seconds"] / 60:.1f} min', '',
             '## Acts', '', '| | count |', '|---|---:|']
    for key in ('decision', 'stage', 'act_type', 'in_force'):
        for name, n in sorted(pop[key].items(), key=lambda x: -x[1]):
            lines.append(f'| {key}: {name} | {n:,} |')
    lines += ['', '## Coverage', '', '| | acts |', '|---|---:|']
    for key in ('title_en', 'title_de', 'with_instrument'):
        lines.append(f'| {key} | {pop[key]:,} |')
    for key in ('text_en', 'text_de', 'recitals_en', 'recitals_de', 'notice', 'eurovoc', 'notice_errors'):
        lines.append(f'| {key} | {stats[key]:,} |')
    lines += ['', '## Not exported', '']
    lines += [f'- {k}: {v:,}' for k, v in info['skipped'].items()] or ['- nothing']
    lines += ['', '## EuroVoc domains (acts)', '']
    lines += [f'- {name}: {n:,}' for name, n in stats['eurovoc_counter'].most_common(25)]
    lines += ['', '## Files', '']
    lines += [f'- `{name}`: {size}' for name, size in info['files'].items()]
    lines += ['', '## Notice fields seen (concept tags, count of notices)', '']
    lines += [f'- {t}: {n:,}' for t, n in stats['concept_tags'].most_common(40)]
    if stats['notice_error_examples']:
        lines += ['', '## Notice errors (examples)', ''] + [f'- {x}' for x in stats['notice_error_examples']]
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def human(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024


def main():
    parser = argparse.ArgumentParser(description='Export EUR-Lex acts to JSON (read-only).')
    parser.add_argument('--env', help='.env file with DATABASE_URL (default: .env here, then ../legal_radar_backend/.env)')
    parser.add_argument('--out', help='output folder (default data/eurlex, or data/eurlex_test with --limit/--celex)')
    parser.add_argument('--check', action='store_true', help='only count, write no data files')
    parser.add_argument('--limit', type=int, default=0, help='export only the first N acts (test)')
    parser.add_argument('--celex', help='comma-separated CELEX numbers to export')
    parser.add_argument('--no-text', action='store_true', help='skip article/annex text (fast)')
    parser.add_argument('--no-notices', action='store_true', help='skip EuroVoc etc. from the stored notices')
    parser.add_argument('--annex-max-chars', type=int, default=50_000, help='shorten each annex (0 = full)')
    parser.add_argument('--batch', type=int, default=200)
    parser.add_argument('--kept-only', action='store_true', help='Include acts whose latest recorded decision is KEEP')
    args = parser.parse_args()

    url, origin = database_url(args.env)
    print(f'Database: {masked(url)}  (from {origin})')
    started = time.time()
    info = {'script': SCRIPT_VERSION, 'started': dt.datetime.now().isoformat(timespec='seconds'),
            'options': {k: v for k, v in vars(args).items() if k != 'env'}}
    conn = connect(url)
    info['database'] = rows(conn, 'SELECT current_database()')[0][0]
    try:
        info['alembic'] = rows(conn, 'SELECT version_num FROM alembic_version')[0][0]
    except psycopg.Error:
        conn.rollback()
        info['alembic'] = 'unknown'

    print('Reading the list of acts and filter decisions ...')
    acts, titles = load_population(conn, args, info)
    counts = profile(conn, acts, info)
    print(f"\nActs: {counts['acts']:,}")
    for key in ('decision', 'stage', 'act_type', 'in_force'):
        print(f'  {key:9}: ' + ', '.join(f'{k} {v:,}' for k, v in counts[key].most_common()))
    print(f"  titles   : EN {counts['title_en']:,}, DE {counts['title_de']:,}")
    print(f"  text     : {counts['acts_with_text']}   notices: {counts['acts_with_notice']}")
    print(f"  skipped  : {info['skipped']}")
    print(f"  db sizes : {counts['table_sizes']}")
    if args.check:
        conn.rollback()
        conn.close()
        print('\n--check: nothing written. Run without --check to export.')
        return 0

    test_run = bool(args.limit or args.celex)       # test runs never overwrite the full export
    out = Path(args.out) if args.out else ROOT / 'data' / ('eurlex_test' if test_run else 'eurlex')
    out.mkdir(parents=True, exist_ok=True)
    names = {'acts': 'acts.jsonl'} if args.no_text else {'acts': 'acts.jsonl', 'EN': 'text_en.jsonl', 'DE': 'text_de.jsonl'}
    parts = {k: out / (v + '.part') for k, v in names.items()}
    writers = {k: p.open('w', encoding='utf-8', newline='\n') for k, p in parts.items()}
    stats = collections.Counter()
    stats['concept_tags'] = collections.Counter()
    stats['eurovoc_counter'] = collections.Counter()
    stats['notice_error_examples'] = []
    last = 0.0
    try:
        for number, batch in enumerate(chunks(acts, max(1, args.batch)), 1):
            export_batch(conn, batch, titles, args, stats, writers)
            now = time.time()
            if now - last > 20 or stats['acts'] == len(acts):
                last = now
                rate = stats['acts'] / max(now - started, 1e-6)
                left = (len(acts) - stats['acts']) / max(rate, 1e-6)
                print(f"  {stats['acts']:,} / {len(acts):,} acts ({100 * stats['acts'] / max(len(acts), 1):.0f}%), "
                      f"{rate:.0f} acts/s, about {left / 60:.0f} min left", flush=True)
    except KeyboardInterrupt:
        for w in writers.values():
            w.close()
        print('\nStopped. Unfinished files end with .part and can be deleted.')
        return 1
    except psycopg.Error as exc:
        for w in writers.values():
            w.close()
        print(f'\nDatabase error: {str(exc).strip()}\nUnfinished files end with .part and can be deleted.')
        if 'statement timeout' in str(exc):
            print('A query took longer than 20 minutes. Try a smaller --batch, e.g. --batch 50.')
        return 2
    finally:
        conn.rollback()
        conn.close()
    for w in writers.values():
        w.close()
    for key, part in parts.items():
        target = out / names[key]
        if target.exists():
            target.unlink()
        part.rename(target)

    info['finished'] = dt.datetime.now().isoformat(timespec='seconds')
    info['seconds'] = round(time.time() - started, 1)
    info['files'] = {names[k]: human((out / names[k]).stat().st_size) for k in names}
    info['stats'] = {k: v for k, v in stats.items() if not isinstance(v, (collections.Counter, list))}
    info['stats']['eurovoc_domains'] = dict(stats['eurovoc_counter'].most_common())
    info['stats']['notice_concept_tags'] = dict(stats['concept_tags'].most_common())
    info['stats']['notice_error_examples'] = stats['notice_error_examples']
    (out / 'export_info.json').write_text(json.dumps(info, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    summary = ROOT / 'reports' / ('eurlex_export_test_summary.md' if test_run else 'eurlex_export_summary.md')
    summary.parent.mkdir(exist_ok=True)
    write_summary(summary, info, stats)
    print(f"\nDone in {info['seconds'] / 60:.1f} min. Files in {out}:")
    for name, size in info['files'].items():
        print(f'  {name:15} {size}')
    print(f'Summary{"" if test_run else " (send me this file)"}: {summary}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
