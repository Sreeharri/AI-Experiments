"""Read-only full EUR-Lex database inventory and evidence snapshot. No AI calls."""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
import copy
import csv
from datetime import date,datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from lr_ai.stored_corpus import validate_celex
spec=importlib.util.spec_from_file_location('corpus_evidence_engine',ROOT/'lr_ai/document_filter.py')
engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)


def write_json(path,value):
    raw=(json.dumps(value,ensure_ascii=False,indent=2,default=str,allow_nan=False)+'\n').encode()
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_bytes(raw);temp.replace(path)
    return hashlib.sha256(raw).hexdigest()


def write_csv(path,rows,fields):
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader()
        for r in rows:
            safe={k:("'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v) for k,v in r.items()}
            w.writerow(safe)


def rank_version(row,as_of):
    future=any(str(row.get(k) or '')[:10]>as_of.isoformat() for k in ('version_date',))
    if row.get('version_kind')=='CONSOLIDATED' and str(row.get('effective_from') or '')[:10]>as_of.isoformat():future=True
    return (bool(row.get('version_id')),bool(row.get('text_characters') or row.get('recital_count')),
            not future,row.get('quality_status')!='REJECTED',
            {'EN':3,'DE':2}.get(row.get('language'),1),bool(row.get('is_preferred')),
            bool(row.get('is_current')),str(row.get('version_date') or ''),str(row.get('fetched_at') or ''),str(row.get('version_id') or ''))


def prefilter_preview(title):
    # Candidates only. These patterns NEVER discard a document.
    if re.search(r'\bappoint(?:ing|ment)\b.*\b(?:member|committee|board)\b',title or '',re.I):
        return 'possible_internal_appointment'
    if re.search(r'\bErnennung\b.*\b(?:Mitglied|Ausschuss)\b',title or '',re.I):
        return 'possible_internal_appointment'
    return ''


def merge_recitals(act):
    combined=[];seen=set()
    for s in act.get('recital_sets') or []:
        for item in s.get('recitals_json') or []:
            if isinstance(item,str):item={'text':item}
            if not isinstance(item,dict):continue
            key=json.dumps(item,sort_keys=True,ensure_ascii=False)
            if key not in seen:seen.add(key);combined.append(item)
    if combined:act['recital_set']={'recitals_json':combined,'recital_count':len(combined)}


def export_snapshot(connection,out,as_of):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    (out/'acts').mkdir();(out/'prepared_evidence').mkdir()
    print('Reading PostgreSQL schema and stored EUR-Lex versions (no AI calls)...',flush=True)
    with connection.cursor() as c:
        c.execute("SELECT table_schema,table_name,column_name,data_type FROM information_schema.columns WHERE table_schema NOT IN ('pg_catalog','information_schema') ORDER BY table_schema,table_name,ordinal_position")
        schema=c.fetchall()
        write_json(out/'database_schema.json',schema)
        c.execute((ROOT/'tools/eurlex_corpus_versions.sql').read_text())
        candidates=c.fetchall()
        c.execute("SELECT d.id::text AS source_document_id,d.external_id,d.document_kind,COALESCE(d.title_en,d.title_de) AS title FROM source_documents d WHERE d.source='EUR_LEX' AND d.deleted_at IS NULL AND NOT EXISTS (SELECT 1 FROM legal_instruments i WHERE i.source_document_id=d.id)")
        unlinked=c.fetchall()
    write_json(out/'unlinked_source_documents.json',unlinked)
    write_csv(out/'stored_versions.csv',candidates,list(candidates[0]) if candidates else ['celex','version_id'])
    groups=defaultdict(list);unusable_ids=[]
    for row in candidates:
        try:key=validate_celex(row.get('celex'))
        except ValueError:
            unusable_ids.append(row);continue
        groups[key].append(row)
    chosen={k:max(rows,key=lambda r:rank_version(r,as_of)) for k,rows in sorted(groups.items())}
    manifest={'format':'eurlex_stored_corpus_v1','complete':False,
              'created_at':datetime.now(timezone.utc).isoformat(),'as_of':as_of.isoformat(),
              'access':'PostgreSQL repeatable-read read-only snapshot','acts':[]}
    inventory=[];sql=(ROOT/'tools/eurlex_corpus_act.sql').read_text()
    for index,(key,row) in enumerate(chosen.items(),1):
        with connection.cursor() as c:
            c.execute(sql,{'instrument_id':row['instrument_id'],'version_id':row.get('version_id')})
            value=c.fetchone()
        if not value:raise ValueError(f'Stored instrument vanished from snapshot: {key}')
        act=next(iter(value.values())) if isinstance(value,dict) else value[0]
        if not isinstance(act,dict):raise ValueError(f'Invalid exported record for {key}')
        act['celex']=key;merge_recitals(act)
        # Keep raw source separate from preparation so omitted fragments and all
        # element types remain available for inspection/re-extraction.
        relative=f'acts/{key}.json'
        digest=write_json(out/relative,act)
        working=copy.deepcopy(act)
        blocks,outline,issues=engine.prepare(working,as_of)
        title=act['document'].get('title_en') or act['document'].get('title_de') or act['instrument'].get('title') or ''
        item={'celex':key,'title':title,'version_id':row.get('version_id'),
              'language':row.get('language'),'document_kind':row.get('document_kind'),
              'in_force':row.get('in_force'),'versions_stored':len(groups[key]),
              'stored_addressees':json.dumps(row.get('document_addressees') or row.get('instrument_addressees'),ensure_ascii=False),
              'stored_territorial_scope':json.dumps(row.get('territorial_scope'),ensure_ascii=False),
              'element_count':row['element_count'],'article_count':row['article_count'],
              'chapter_count':row['chapter_count'],'annex_count':row['annex_count'],
              'recital_count':row['recital_count'],'usable_chunks':len(blocks),
              'prepared_characters':sum(len(b['text']) for b in blocks),
              'text_status':'available' if blocks else 'unresolved_no_usable_text',
              'missing_title':not bool(title.strip()),'prefilter_candidate':prefilter_preview(title)}
        write_json(out/'prepared_evidence'/f'{key}.json',{'celex':key,'version':act['version'],
                   'chunks':blocks,'outline':outline,'issues':issues,
                   'preparation':working.get('_evidence_preparation'),
                   'note':'All prepared stored text; exact per-request evidence is saved by inference.'})
        inventory.append(item)
        manifest['acts'].append({'celex':key,'file':relative,'sha256':digest,
                                 'version_id':row.get('version_id'),'text_status':item['text_status']})
        if index%100==0:
            print(f'Exported {index}/{len(chosen)} acts',flush=True)
            write_json(out/'export_progress.json',{'exported':index,'total':len(chosen),'complete':False})
    columns=list(inventory[0]) if inventory else ['celex','text_status']
    write_csv(out/'inventory.csv',inventory,columns)
    write_csv(out/'prefilter_candidates.csv',[r for r in inventory if r['prefilter_candidate']],columns)
    write_csv(out/'unresolved_text.csv',[r for r in inventory if not r['usable_chunks']],columns)
    write_json(out/'unusable_identifiers.json',unusable_ids)
    summary={'unique_acts':len(chosen),'stored_version_rows':len(candidates),
             'unlinked_source_documents':len(unlinked),'unusable_identifier_rows':len(unusable_ids),
             'with_text':sum(bool(r['usable_chunks']) for r in inventory),
             'without_text':sum(not r['usable_chunks'] for r in inventory),
             'without_recitals':sum(not r['recital_count'] for r in inventory),
             'without_chapters':sum(not r['chapter_count'] for r in inventory),
             'without_articles':sum(not r['article_count'] for r in inventory),
             'short_prepared_text_under_10000_chars':sum(0<r['prepared_characters']<10000 for r in inventory),
             'by_document_kind':dict(Counter(str(r['document_kind']) for r in inventory)),
             'by_language':dict(Counter(str(r['language']) for r in inventory)),
             'prefilter_preview_matches':sum(bool(r['prefilter_candidate']) for r in inventory),
             'automatic_relevance_exclusions':0,
             'note':'One version per canonical CELEX; all version metadata is retained in stored_versions.csv. No structural eligibility gate.'}
    manifest['complete']=True;manifest['summary']=summary
    write_json(out/'corpus_manifest.json',manifest)
    write_json(out/'inventory_summary.json',summary)
    write_json(out/'export_progress.json',{'exported':len(chosen),'total':len(chosen),'complete':True})
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    return manifest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env',type=Path,default=ROOT/'.env')
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--as-of',type=date.fromisoformat,default=date.today())
    args=p.parse_args()
    try:
        import os
        from dotenv import dotenv_values
        import psycopg
        from psycopg.rows import dict_row
        values=dotenv_values(args.env) if args.env.exists() else {}
        url=os.environ.get('DATABASE_URL') or values.get('DATABASE_URL')
        if not url:raise ValueError('DATABASE_URL is missing from the environment/.env')
        url=url.replace('postgresql+psycopg://','postgresql://',1).replace('postgresql+psycopg2://','postgresql://',1)
        with psycopg.connect(url,row_factory=dict_row,connect_timeout=20) as connection:
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            connection.execute("SET LOCAL statement_timeout = '15min'")
            export_snapshot(connection,args.out,args.as_of)
    except (OSError,ValueError,ImportError) as exc:p.exit(1,f'Database export stopped: {exc}\n')
    except Exception as exc:
        # Do not print a driver exception containing a DSN, user or password.
        p.exit(1,f'Database export failed ({type(exc).__name__}). Check PostgreSQL access, schema compatibility and the partial output. No AI inference was started.\n')


if __name__=='__main__':main()
