import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('labelling_export',ROOT/'tools/export_labelling_excel.py')
e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)


class ExportTests(unittest.TestCase):
    def test_profile_mapping_entries_preserved(self):
        value={'equipment':{'scope':'Machines'}}
        self.assertEqual(list(value.items()),e.profile_entries(value))

    def test_list_based_not_roles_preserved(self):
        value=['bank','machinery_manufacturer']
        self.assertEqual([('bank','bank'),('machinery_manufacturer','machinery_manufacturer')],
                         e.profile_entries(value))

    def test_list_of_profile_objects_and_empty_sections(self):
        value=[{'id':'bank','note':'excluded'},{'scope':'Unkeyed original entry'}]
        entries=e.profile_entries(value)
        self.assertEqual('bank',entries[0][0])
        self.assertEqual('entry_2',entries[1][0])
        self.assertEqual(value,[entry[1] for entry in entries])
        self.assertEqual([],e.profile_entries(None))
        self.assertEqual([],e.profile_entries([]))

    def test_text_parts_preserve_every_character(self):
        text=('A line\nÜnicode text = 10; '*300)
        self.assertEqual(text,''.join(e.parts(text)))
        self.assertTrue(all(len(p)<=1200 for p in e.parts(text)))

    def test_evidence_preserves_source_ids_and_passages(self):
        records=[{'celex':'32024R0001','evidence':[{'source_id':'article/1#chunk1',
                   'path':'article/1','type':'ARTICLE','text':'x'*3000}]}]
        rows,starts=e.evidence_rows(records)
        self.assertEqual(2,starts['32024R0001'])
        self.assertEqual('x'*3000,''.join(r[7] for r in rows))
        self.assertEqual([1,2,3],[r[5] for r in rows])
        self.assertTrue(all(r[2]=='article/1#chunk1' for r in rows))

    def test_input_fingerprint_enforced(self):
        with tempfile.TemporaryDirectory() as temporary:
            p=Path(temporary)/'acts.json';raw=b'[{"celex":"32024R0001"}]';p.write_bytes(raw)
            self.assertIn('32024R0001',e.read_acts(p,e.sha(raw)))
            with self.assertRaises(ValueError):e.read_acts(p,'wrong')

    def test_manifest_ids_and_order_enforced(self):
        with tempfile.TemporaryDirectory() as temporary:
            p=Path(temporary)
            (p/'manifest.json').write_text(json.dumps({'selected_celex':['b','a']}))
            for key in ['a','b']:(p/f'{key}.json').write_text(json.dumps({'celex':key}))
            self.assertEqual(['b','a'],[r['celex'] for r in e.read_run(p)[1]])
            (p/'a.json').unlink()
            with self.assertRaises(ValueError):e.read_run(p)

    def test_control_characters_visible_not_deleted(self):
        self.assertEqual('a\\u0001b',e.excel_text('a\x01b'))


if __name__=='__main__':unittest.main()
