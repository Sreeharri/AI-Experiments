#!/usr/bin/env python3
"""Describe the Legal Radar database (READ-ONLY) so the EUR-Lex export can be written.

What it does:
- connects with DATABASE_URL from .env in a read-only session
- lists every table and view with its columns, types, row counts and keys
- for tables that look like EUR-Lex / legal-act data: shows the most common
  values of type-like columns and two example rows (long text shortened)
- writes everything to reports/database_structure.md

It changes nothing in the database. Columns that look like passwords, tokens,
keys or e-mail addresses are never printed.

Usage (in the AI experiments folder):
    py tools\\describe_database.py
Needs:  py -3 -m pip install "psycopg[binary]"
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lr_ai.qwen_client import load_env  # noqa: E402

try:
    import psycopg
except ImportError:
    sys.exit('psycopg is missing. Run:  py -3 -m pip install "psycopg[binary]"')

# Tables whose name or columns contain one of these words get example rows.
INTEREST = re.compile(r'eurlex|eur_lex|celex|cellar|legal|act|document|element|article|recital|annex|'
                      r'relation|link|eurovoc|title|text|gist|block|finding|catalog|source|work|expression|'
                      r'version|consolidat|amend|repeal|run_report|agent', re.I)
# Never print values of these columns.
SECRET = re.compile(r'pass|secret|token|api_?key|hash|salt|e_?mail|session|cookie|credential', re.I)
# Show the most common values of these columns (helps find EUR-Lex rows in shared tables).
TYPE_LIKE = re.compile(r'^(source|source_id|source_key|source_code|source_name|origin|document_type|doc_type|'
                       r'resource_type|form|type|kind|element_type|level|language|lang|status|in_force|'
                       r'operation|relation_type|link_type|sector)$', re.I)
MAX_TEXT = 220


def connection_string():
    load_env(ROOT / '.env')
    url = os.getenv('DATABASE_URL', '').strip()
    if not url:
        sys.exit('DATABASE_URL is not set in .env')
    # SQLAlchemy style "postgresql+psycopg://" -> plain "postgresql://"
    return re.sub(r'^postgres(ql)?\+\w+://', 'postgresql://', url)


def short(value):
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f'<{len(bytes(value))} bytes>'
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    text = str(value).replace('\r', ' ').replace('\n', ' ')
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + f'… ({len(text)} chars)'


def q(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchall()


def ident(schema, name):
    return '"' + schema.replace('"', '""') + '"."' + name.replace('"', '""') + '"'


def main():
    out = ROOT / 'reports' / 'database_structure.md'
    out.parent.mkdir(exist_ok=True)
    lines = [f'# Database structure ({dt.datetime.now():%Y-%m-%d %H:%M})', '',
             'Read-only description for writing the EUR-Lex export. Long text is shortened.', '']
    with psycopg.connect(connection_string(), autocommit=False) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '120s'")
            version = q(cur, 'select version()')[0][0]
            db = q(cur, 'select current_database()')[0][0]
            lines += [f'- Database: `{db}`', f'- Server: {version.split(",")[0]}', '']

            tables = q(cur, """
                select table_schema, table_name, table_type from information_schema.tables
                where table_schema not in ('pg_catalog', 'information_schema') and table_schema not like 'pg_toast%%'
                order by table_schema, table_name""")
            columns = q(cur, """
                select table_schema, table_name, column_name, data_type, udt_name, is_nullable
                from information_schema.columns
                where table_schema not in ('pg_catalog', 'information_schema')
                order by table_schema, table_name, ordinal_position""")
            constraints = q(cur, """
                select n.nspname, c.relname, con.contype, pg_get_constraintdef(con.oid)
                from pg_constraint con join pg_class c on c.oid = con.conrelid
                join pg_namespace n on n.oid = c.relnamespace
                where n.nspname not in ('pg_catalog', 'information_schema') and con.contype in ('p', 'f', 'u')
                order by 1, 2, 3""")
            indexes = q(cur, """
                select schemaname, tablename, indexdef from pg_indexes
                where schemaname not in ('pg_catalog', 'information_schema') order by 1, 2""")

            cols = {}
            for schema, table, name, dtype, udt, nullable in columns:
                cols.setdefault((schema, table), []).append((name, dtype if dtype != 'USER-DEFINED' else udt, nullable))
            cons = {}
            for schema, table, kind, definition in constraints:
                cons.setdefault((schema, table), []).append(({'p': 'PK', 'f': 'FK', 'u': 'UNIQUE'}[kind], definition))
            idx = {}
            for schema, table, definition in indexes:
                idx.setdefault((schema, table), []).append(definition)

            lines += ['## Overview', '', '| Table | Type | Rows | Columns |', '|---|---|---:|---:|']
            counts = {}
            for schema, table, ttype in tables:
                try:
                    counts[(schema, table)] = q(cur, f'select count(*) from {ident(schema, table)}')[0][0]
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    conn.read_only = True
                    cur.execute("SET statement_timeout = '120s'")
                    counts[(schema, table)] = f'error: {short(exc)}'
                lines.append(f'| `{schema}.{table}` | {ttype.lower()} | {counts[(schema, table)]} | '
                             f'{len(cols.get((schema, table), []))} |')
            lines.append('')

            for schema, table, ttype in tables:
                key = (schema, table)
                column_list = cols.get(key, [])
                interesting = bool(INTEREST.search(table)) or any(INTEREST.search(c[0]) for c in column_list)
                lines += [f'## `{schema}.{table}`', '', f'{ttype.lower()}, {counts[key]} rows', '',
                          '| Column | Type | Null |', '|---|---|---|']
                lines += [f'| `{n}` | {t} | {"yes" if nl == "YES" else "no"} |' for n, t, nl in column_list]
                lines.append('')
                for kind, definition in cons.get(key, []):
                    lines.append(f'- {kind}: `{definition}`')
                for definition in idx.get(key, []):
                    lines.append(f'- index: `{definition}`')
                lines.append('')
                if not interesting or not isinstance(counts[key], int) or counts[key] == 0:
                    continue
                visible = [c[0] for c in column_list if not SECRET.search(c[0])]
                for name in [c[0] for c in column_list if TYPE_LIKE.match(c[0]) and not SECRET.search(c[0])]:
                    col = '"' + name.replace('"', '""') + '"'
                    try:
                        rows = q(cur, f'select {col}::text, count(*) from {ident(schema, table)} '
                                      f'group by 1 order by 2 desc limit 15')
                        lines.append(f'Most common `{name}`: ' + ', '.join(f'`{short(v)}` ({n})' for v, n in rows))
                    except Exception:  # noqa: BLE001
                        conn.rollback()
                        conn.read_only = True
                lines.append('')
                if visible:
                    select = ', '.join('"' + v.replace('"', '""') + '"' for v in visible)
                    try:
                        rows = q(cur, f'select {select} from {ident(schema, table)} limit 2')
                        for number, row in enumerate(rows, 1):
                            lines.append(f'Example row {number}:')
                            lines.append('```json')
                            lines.append(json.dumps({k: short(v) for k, v in zip(visible, row)},
                                                    ensure_ascii=False, indent=1))
                            lines.append('```')
                    except Exception as exc:  # noqa: BLE001
                        conn.rollback()
                        conn.read_only = True
                        lines.append(f'(example rows not readable: {short(exc)})')
                lines.append('')
            conn.rollback()   # nothing to commit; read-only anyway
    out.write_text('\n'.join(lines), encoding='utf-8')
    print(f'{len(tables)} tables described. Send me this file:\n{out}')


if __name__ == '__main__':
    main()
