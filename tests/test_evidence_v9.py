import importlib.util
import json
from pathlib import Path
from datetime import date
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from extract_evidence_inputs import extract_inputs,engine


class EvidenceTests(unittest.TestCase):
    def act(self):
        return {'celex':'32024R0001','version':{'id':'v1'},'document':{'title_en':'Example'},'instrument':{},
                'elements':[{'path':'article/1','element_type':'ARTICLE','body_text':'Article 16 Article 18'},
                            {'path':'p/1','element_type':'PARAGRAPH','body_text':'Companies have access to the requested information.'},
                            {'path':'p/2','element_type':'PARAGRAPH','body_text':'Companies have access to the requested information.'}]}

    def test_fragment_omission_is_audited_and_substantive_text_retained(self):
        a=self.act();blocks,_,_=engine.prepare(a,date(2026,11,4))
        self.assertTrue(any('Companies have access' in b['text'] for b in blocks))
        self.assertFalse(any(b['text']=='Article 16 Article 18' for b in blocks))
        audit=a['_evidence_preparation']['omitted_chunks']
        self.assertTrue(any(x['reason']=='reference_only' for x in audit))
        self.assertTrue(any(x['reason']=='exact_duplicate' for x in audit))

    def test_full_text_fallback_retains_text_even_without_elements(self):
        a=self.act();a['elements']=[];a['document']['full_text']='Stored body text contains the company rights.'
        blocks,_,issues=engine.prepare(a,date(2026,11,4))
        self.assertTrue(blocks)
        self.assertIn('company rights',blocks[0]['text'])
        self.assertNotIn('No stored source text',issues)

    def test_order_covers_all_chunks_beyond_old_cap(self):
        items=list(range(500))
        order=engine.spread_order(items)
        self.assertEqual(len(order),500)
        self.assertEqual(set(order),set(items))
        self.assertIn(499,order[:4])

    def test_bad_aggregate_parent_does_not_hide_child_body(self):
        a=self.act();a['elements']=[
            {'path':'article/1','element_type':'ARTICLE','body_text':'Article 16 Article 18','details':{'aggregate_body':True}},
            {'path':'article/1/p/1','element_type':'PARAGRAPH','body_text':'Substantive child text must still reach the model.'}]
        blocks,_,_=engine.prepare(a,date(2026,11,4))
        self.assertTrue(any('Substantive child text' in b['text'] for b in blocks))

    def test_id_extraction_preserves_original_and_fails_for_missing_ids(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source=root/'source.json';a=self.act()
            source.write_text(json.dumps([a]));original=source.read_bytes()
            target=extract_inputs(source,[a['celex']],root/'extracted',date(2026,11,4))
            self.assertEqual(source.read_bytes(),original)
            data=json.loads(target.read_text())
            self.assertEqual(data[0]['elements'],a['elements'])
            self.assertTrue((root/'extracted/prepared_evidence'/f"{a['celex']}.json").exists())
            with self.assertRaises(ValueError):
                extract_inputs(source,['32024R9999'],root/'missing',date(2026,11,4))
            self.assertFalse((root/'missing').exists())


if __name__=='__main__':unittest.main()
