import contextlib
from datetime import date
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tools'))
from lr_ai.stored_corpus import StoredCorpus,validate_celex
from export_eurlex_corpus import export_snapshot,rank_version,prefilter_preview
from screen_eurlex_corpus import exclusions
import screen_eurlex_corpus as screen


class FakeConnection:
    def __init__(self,rows,acts):self.rows=rows;self.acts=acts;self.queries=[]
    def cursor(self):return FakeCursor(self)


class FakeCursor:
    def __init__(self,c):self.c=c
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql,params=None):
        self.c.queries.append(sql)
        if 'information_schema.columns' in sql:self.data=[]
        elif 'WITH shapes' in sql:self.data=self.c.rows
        elif 'NOT EXISTS' in sql:self.data=[{'source_document_id':'unlinked','external_id':'missing'}]
        else:self.data=[{'act':self.c.acts[params['instrument_id']]}]
    def fetchall(self):return self.data
    def fetchone(self):return self.data[0] if self.data else None


def row(key,typ='REGULATION'):
    return {'instrument_id':key,'celex':key,'title':'Example','source_document_id':'d',
            'document_kind':typ,'document_date':None,'in_force':True,'version_id':'v',
            'language':'EN','version_kind':'ORIGINAL','version_date':None,'effective_from':None,
            'is_preferred':True,'is_current':True,'fetched_at':None,'quality_status':None,
            'element_count':1,'article_count':0,'chapter_count':0,'annex_count':0,
            'recital_count':0,'text_characters':50}


def act(key,body='Available substantive text without chapter or recital headings.'):
    return {'celex':key,'document':{'title_en':'Example'},'instrument':{},'version':{'id':'v'},
            'elements':[{'path':'p/1','element_type':'PARAGRAPH','body_text':body}],
            'recital_sets':[]}


class CorpusTests(unittest.TestCase):
    def test_all_shapes_and_types_export_without_old_eligibility_gates(self):
        ids=['32024R0001','32024L0002','32024D0003','32024H0004']
        rows=[row(x) for x in ids]
        acts={x:act(x) for x in ids};acts[ids[-1]]=act(ids[-1],'')
        connection=FakeConnection(rows,acts)
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'corpus'
            with contextlib.redirect_stdout(io.StringIO()):manifest=export_snapshot(connection,out,date(2026,11,5))
            corpus=StoredCorpus(out)
            self.assertEqual(corpus.ids(),sorted(ids))
            self.assertEqual(corpus.get(ids[0])['elements'],acts[ids[0]]['elements'])
            self.assertEqual(manifest['summary']['without_text'],1)
            self.assertEqual(manifest['summary']['without_recitals'],4)
            self.assertEqual(manifest['summary']['unlinked_source_documents'],1)
            self.assertEqual(manifest['summary']['automatic_relevance_exclusions'],0)
            self.assertTrue((out/'prepared_evidence'/f'{ids[0]}.json').exists())
            p=out/next(x['file'] for x in manifest['acts'] if x['celex']==ids[0]);p.write_text('{}')
            with self.assertRaises(ValueError):corpus.get(ids[0])
        self.assertTrue(all(sql.lstrip().startswith(('SELECT','--')) for sql in connection.queries))

    def test_version_selection_prefers_usable_text_and_preserves_language_fallback(self):
        en=row('32024R0001');en['text_characters']=0
        de=dict(en,language='DE',text_characters=100)
        self.assertGreater(rank_version(de,date(2026,11,5)),rank_version(en,date(2026,11,5)))
        future=dict(de,version_kind='CONSOLIDATED',version_date='2027-01-01')
        self.assertGreater(rank_version(de,date(2026,11,5)),rank_version(future,date(2026,11,5)))

    def test_preview_does_not_treat_foreign_country_as_exclusion(self):
        self.assertEqual(prefilter_preview('Restrictions on imports from Russia'),'')
        self.assertEqual(prefilter_preview('Rules for France and Germany'),'')
        self.assertEqual(prefilter_preview('Appointing a member of the committee'),'possible_internal_appointment')

    def test_exclusions_require_known_ids_and_reason(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'approved.csv'
            p.write_text('celex,reason\n32024R0001,Reviewed internal appointment\n')
            self.assertEqual(len(exclusions(p,{'32024R0001'})),1)
            with self.assertRaises(ValueError):exclusions(p,{'32024R0002'})
            p.write_text('celex,reason\n32024R0001,\n')
            with self.assertRaises(ValueError):exclusions(p,{'32024R0001'})

    def test_unsafe_identifier_and_incomplete_export_rejected(self):
        self.assertEqual(validate_celex('32024H0001'),'32024H0001')
        with self.assertRaises(ValueError):validate_celex('../../bad')
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);(p/'corpus_manifest.json').write_text(json.dumps({'format':'eurlex_stored_corpus_v1','complete':False}))
            with self.assertRaises(ValueError):StoredCorpus(p)

    def test_full_screening_uses_ids_file_and_resume_reuses_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source=root/'corpus';output=root/'run'
            ids=['32024R0001','32024H0004']
            with contextlib.redirect_stdout(io.StringIO()):
                export_snapshot(FakeConnection([row(x) for x in ids],{x:act(x) for x in ids}),source,date(2026,11,5))
            calls=[]
            def fake_run(command,**kwargs):
                calls.append(command)
                self.assertIn('--ids-file',command);self.assertNotIn('--celex',command)
                selected=Path(command[command.index('--ids-file')+1]).read_text().splitlines()
                destination=Path(command[command.index('--out')+1]);destination.mkdir(exist_ok=True)
                for key in selected:
                    (destination/f'{key}.json').write_text(json.dumps({'celex':key,'status':'ok',
                        'decision':{'level':'INDIRECT','reason':'Fixture only.','anchor_ids':[]}}))
                return 0
            with patch.object(screen.subprocess,'call',fake_run),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(screen.main(['--input',str(source),'--out',str(output)]),0)
                self.assertEqual(screen.main(['--input',str(source),'--out',str(output),'--resume']),0)
            summary=json.loads((output/'corpus_summary.json').read_text())
            self.assertEqual(summary['selected'],2)
            self.assertEqual(summary['counts'],{'INDIRECT':2})
            self.assertEqual(len(calls),2)


if __name__=='__main__':unittest.main()
