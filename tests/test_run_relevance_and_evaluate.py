import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("relevance_pipeline", TOOLS / "run_relevance_and_evaluate.py")
pipeline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pipeline)

# Test fixture only: a subprocess with the same file/CLI contract as the runner.
# It never calls a model or appears in delivered prediction results.
FAKE_RUNNER = '''
import argparse, json
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--input'); p.add_argument('--celex'); p.add_argument('--out')
a,_=p.parse_known_args()
ids=a.celex.split(','); out=Path(a.out)
data=json.loads(Path(a.input).read_text())
failed=False
for act in data:
 if act['celex'] not in ids: continue
 bad=act.get('test_failure',False)
 failed=failed or bad
 dec={'level':act['test_prediction'],'reason':'fixture reason','anchor_ids':[]}
 d={'celex':act['celex'],'status':'failed' if bad else 'ok',
    'decision':dec,'first_pass_decision':dec,
    'verification':{'required':False,'completed':False}}
 (out/(act['celex']+'.json')).write_text(json.dumps(d))
(out/'manifest.json').write_text(json.dumps({'selected_celex':ids}))
print('Fixture subprocess finished',flush=True)
raise SystemExit(1 if failed else 0)
'''


class PipelineTests(unittest.TestCase):
    def setup_project(self, root, fail=False):
        (root / 'tools').mkdir()
        (root / 'config').mkdir()
        (root / 'prompts').mkdir()
        (root / 'prompts/document_relevance.txt').write_text('Explicit offline test prompt')
        (root / 'prompts/review_ignore.txt').write_text('Explicit offline review prompt')
        (root / 'tools/document_relevance.py').write_text(FAKE_RUNNER)
        (root / 'config/business_anchors_en.yaml').write_text('anchors: {}')
        (root / 'config/domains.yaml').write_text('domains: {}')
        (root / '.env').write_text('TEST_ONLY=1')
        labels = root / 'labels.csv'
        labels.write_text('ID,Title,Label,LLM label,Astro label\n'
                          'A,Act A,DIRECT,IGNORE,IGNORE\n'
                          'B,Act B,INDIRECT,IGNORE,IGNORE\n'
                          'C,Act C,IGNORE,DIRECT,DIRECT\n'
                          'D,Act D,IGNORE,DIRECT,DIRECT\n')
        acts = [{'celex':i, 'test_prediction':lev} for i,lev in
                [('A','INDIRECT'),('B','DIRECT'),('C','IGNORE'),('D','DIRECT'),('E','DIRECT')]]
        if fail:
            acts[0]['test_failure']=True
        source = root / 'eligible_acts.json'
        source.write_text(json.dumps(acts))
        return labels, source

    def run_pipeline(self, root, labels, source, output):
        with patch.object(pipeline, 'ROOT', root), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return pipeline.main(['--labels',str(labels),'--input',str(source),'--out',str(output)])

    def test_inference_then_evaluation_and_no_truth_leak(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            labels,source=self.setup_project(root)
            before=labels.read_bytes()
            output=root/'fresh'
            self.assertEqual(self.run_pipeline(root,labels,source,output),0)
            metrics=json.loads((output/'evaluation/metrics.json').read_text())
            state=json.loads((output/'pipeline.json').read_text())
            self.assertEqual(metrics['confusion_matrix'],{'TP':2,'FP':1,'FN':0,'TN':1})
            self.assertAlmostEqual(metrics['metrics']['f1'],0.8)
            self.assertEqual(state['selected_ids'],['A','B','C','D'])
            self.assertEqual(state['status'],'completed')
            self.assertEqual(labels.read_bytes(),before)
            self.assertEqual((output/'ground_truth/labels.csv').read_bytes(),before)
            self.assertNotIn(str(labels),state['classification_command'])
            self.assertNotIn('--labels',state['classification_command'])
            self.assertFalse((output/'predictions/E.json').exists())
            self.assertTrue((output/'inference.log').is_file())
            self.assertTrue((output/'evaluation_first_pass/metrics.json').is_file())
            self.assertTrue((output/'comparison.json').is_file())
            self.assertEqual((output/'prompts_used/document_relevance.txt').read_text(),
                             'Explicit offline test prompt')
            self.assertEqual(state['decision_prompt_sha256'],
                             pipeline.file_hash(root/'prompts/document_relevance.txt'))

    def test_failed_inference_still_runs_evaluation_without_inflated_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            labels,source=self.setup_project(root,fail=True)
            output=root/'fresh'
            self.assertEqual(self.run_pipeline(root,labels,source,output),2)
            metrics=json.loads((output/'evaluation/metrics.json').read_text())
            self.assertEqual(metrics['unresolved_acts'],1)
            self.assertIsNone(metrics['metrics'])
            self.assertTrue((output/'evaluation/unresolved.csv').exists())
            self.assertEqual(json.loads((output/'pipeline.json').read_text())['status'],'incomplete_or_failed')

    def test_existing_output_cannot_reuse_old_predictions(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            labels,source=self.setup_project(root)
            output=root/'existing'
            output.mkdir()
            (output/'keep.txt').write_text('old')
            self.assertEqual(self.run_pipeline(root,labels,source,output),1)
            self.assertEqual((output/'keep.txt').read_text(),'old')
            self.assertFalse((output/'predictions').exists())

    def test_invalid_truth_fails_before_inference(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            labels,source=self.setup_project(root)
            labels.write_text('ID,Label\nA,\n')
            output=root/'fresh'
            self.assertEqual(self.run_pipeline(root,labels,source,output),1)
            self.assertFalse(output.exists())

    def test_input_pointer_relative_and_explicit(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            cache=root/'data/cache'
            cache.mkdir(parents=True)
            source=cache/'eligible_acts.json'
            source.write_text('[]')
            pointer=root/'data/pointer.json'
            pointer.write_text(json.dumps({'eligible_cache':'data/cache'}))
            self.assertEqual(pipeline.locate_input(None,pointer,root),source)
            self.assertEqual(pipeline.locate_input(source,root/'missing.json',root),source)
            with self.assertRaises(ValueError):
                pipeline.locate_input(None,root/'missing.json',root)


if __name__=='__main__':
    unittest.main()
