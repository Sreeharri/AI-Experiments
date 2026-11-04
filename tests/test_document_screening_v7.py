import importlib.util
import json
from pathlib import Path
from datetime import date
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('screening_v7_test_engine', ROOT / 'lr_ai/document_filter.py')
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)


class Client:
    def __init__(self): self.calls = []
    def tokenize(self, **kwargs): return 100
    def clean(self, value): return value
    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        class Response:
            ok = True
            content = '{"level":"INDIRECT","reason":"Purchased equipment is affected.","anchor_ids":["equipment"]}'
            finish_reason = 'stop'
            def parse_json(self): return json.loads(self.content), None
        return Response()


def act():
    return {'celex':'32024R0001', 'version':{'id':'v1'}, 'document':{'title_en':'Equipment rules'},
            'instrument':{}, 'elements':[{'path':f'plain/{i}', 'element_type':'PARAGRAPH',
                                         'body_text':f'Stored provision number {i}.'} for i in range(50)]}


class ScreeningTests(unittest.TestCase):
    def test_exact_three_field_and_three_level_contract(self):
        self.assertEqual({'level','reason','anchor_ids'},set(f.SCHEMA['properties']))
        self.assertEqual(['DIRECT','INDIRECT','IGNORE'],f.SCHEMA['properties']['level']['enum'])

    def test_plain_text_without_article_or_chapter_headings_reaches_model(self):
        c=Client()
        result=f.run_act(c,act(),{'anchors':{'equipment':{}}},date(2026,11,4),40960)
        self.assertEqual('ok',result['status'])
        self.assertEqual('INDIRECT',result['decision']['level'])
        self.assertEqual(1,len(c.calls))
        self.assertTrue(result['evidence'])

    def test_selection_is_independent_of_profile_words_and_headings(self):
        blocks,_,_=f.prepare(act(),date(2026,11,4))
        before=f.initial_selection(blocks,{'anchors':{'steel':{}}})
        for b in blocks:b['heading']='Scope chemicals construction employer'
        self.assertEqual(before,f.initial_selection(blocks,{'anchors':{'unrelated':{}}}))

    def test_sample_covers_beginning_middle_and_end(self):
        blocks,_,_=f.prepare(act(),date(2026,11,4))
        chosen=f.initial_selection(blocks,{})[:24]
        paths={b['path'] for b in blocks if b['source_id'] in chosen}
        self.assertIn('plain/0',paths)
        self.assertIn('plain/49',paths)
        self.assertTrue(any(f'plain/{i}' in paths for i in range(20,30)))

    def test_orphan_body_text_not_lost_when_recitals_exist(self):
        a=act();a['elements'].insert(0,{'path':'recital/1','element_type':'RECITAL','body_text':'Purpose.'})
        blocks,_,_=f.prepare(a,date(2026,11,4))
        self.assertTrue(any(b['type']=='DOCUMENT_TEXT' and 'provision' in b['text'] for b in blocks))

    def test_anchor_vocabulary_sent_to_server_without_rewriting_decision(self):
        c=Client()
        result=f.run_act(c,act(),{'valid_anchor_ids':['equipment'],'anchors':{'equipment':{}}},date(2026,11,4),40960)
        schema=c.calls[0][1]['schema']
        self.assertEqual(['equipment'],schema['properties']['anchor_ids']['items']['enum'])
        self.assertEqual('Purchased equipment is affected.',result['decision']['reason'])


if __name__=='__main__':unittest.main()
