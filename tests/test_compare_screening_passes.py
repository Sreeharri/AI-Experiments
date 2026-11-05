import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
from compare_screening_passes import evaluate_both_passes


def dec(level):
    return {'level': level, 'reason': 'Test business connection.', 'anchor_ids': []}


class PassComparisonTests(unittest.TestCase):
    def test_measures_recovered_relevance_and_added_false_positives(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = root / 'predictions'
            run.mkdir()
            labels = root / 'labels.csv'
            labels.write_text('ID,Title,Label\nA,Act A,DIRECT\nB,Act B,IGNORE\nC,Act C,IGNORE\nD,Act D,INDIRECT\n')
            for identifier, first, final in [('A', 'IGNORE', 'INDIRECT'), ('B', 'IGNORE', 'DIRECT'),
                                             ('C', 'IGNORE', 'IGNORE'), ('D', 'DIRECT', 'DIRECT')]:
                reviewed = first == 'IGNORE'
                value = {'celex': identifier, 'status': 'ok', 'first_pass_decision': dec(first),
                         'decision': dec(final), 'verification': {'required': reviewed, 'completed': reviewed}}
                (run / f'{identifier}.json').write_text(json.dumps(value))
            before = (run / 'A.json').read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):
                code = evaluate_both_passes(labels, 'Labels', run, root)
            result = json.loads((root / 'comparison.json').read_text())
            self.assertEqual(code, 0)
            self.assertEqual(result['recovered_relevant_ids'], ['A'])
            self.assertEqual(result['additional_false_positive_ids'], ['B'])
            self.assertEqual(result['review_completed_count'], 3)
            self.assertEqual(result['first_confusion_matrix'], {'TP': 1, 'FP': 0, 'FN': 1, 'TN': 2})
            self.assertEqual(result['final_confusion_matrix'], {'TP': 2, 'FP': 1, 'FN': 0, 'TN': 1})
            self.assertAlmostEqual(result['metric_changes']['recall'], 0.5)
            self.assertEqual((run / 'A.json').read_bytes(), before)

    def test_failed_review_preserves_first_pass_score_but_final_is_unresolved(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = root / 'predictions'
            run.mkdir()
            labels = root / 'labels.csv'
            labels.write_text('ID,Label\nA,DIRECT\n')
            (run / 'A.json').write_text(json.dumps({'celex': 'A', 'status': 'failed',
                'first_pass_decision': dec('IGNORE'), 'verification': {'required': True, 'completed': False},
                'error': 'Second call unavailable'}))
            with contextlib.redirect_stdout(io.StringIO()):
                code = evaluate_both_passes(labels, 'Labels', run, root)
            result = json.loads((root / 'comparison.json').read_text())
            self.assertEqual(code, 2)
            self.assertIsNotNone(result['first_metrics'])
            self.assertIsNone(result['final_metrics'])
            self.assertEqual(result['review_failed_ids'], ['A'])


if __name__ == '__main__':
    unittest.main()
