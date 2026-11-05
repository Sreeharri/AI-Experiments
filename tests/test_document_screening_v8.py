import ast
from datetime import date
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('screening_v8_engine', ROOT / 'lr_ai/document_filter.py')
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)


def decision(level, reason='Equipment or business rights are affected.'):
    return {'reason': reason, 'anchor_ids': [] if level == 'IGNORE' else ['equipment'], 'level': level}


def act():
    return {'celex': '32024R0001', 'version': {'id': 'v1'},
            'document': {'title_en': 'Equipment and business rules'}, 'instrument': {},
            'elements': [{'path': f'plain/{i}', 'element_type': 'PARAGRAPH',
                          'body_text': f'Stored provision number {i}.'} for i in range(50)]}


PROFILE = {'valid_anchor_ids': ['equipment'], 'anchors': {'equipment': {'scope': 'Work equipment'}},
           'not_roles': 'Not an equipment manufacturer.'}


class Client:
    def __init__(self, responses, limited=False):
        self.responses = list(responses)
        self.calls = []
        self.limited = limited

    def clean(self, text):
        return text

    def tokenize(self, *, messages):
        if not self.limited:
            return 100
        n = len(json.loads(messages[1]['content'])['excerpts'])
        return 200 + 100 * n + (100 if 'IGNORE REVIEW' in messages[0]['content'] else 0)

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        class Response:
            ok = True
            finish_reason = 'stop'
            reasoning = ''
            content = json.dumps(value)
            def parse_json(self):
                return value, None
        return Response()


class IgnoreReviewTests(unittest.TestCase):
    def run_act(self, client, **kwargs):
        return f.run_act(client, act(), PROFILE, date(2026, 11, 4), 40960, **kwargs)

    def test_positive_levels_receive_one_call(self):
        for lev in ('DIRECT', 'INDIRECT'):
            c = Client([decision(lev)])
            r = self.run_act(c)
            self.assertEqual(len(c.calls), 1)
            self.assertEqual(r['decision'], decision(lev))
            self.assertEqual(r['first_pass_decision'], r['decision'])
            self.assertFalse(r['verification']['required'])

    def test_ignore_is_reviewed_once_and_model_can_retain(self):
        for lev in ('DIRECT', 'INDIRECT'):
            c = Client([decision('IGNORE'), decision(lev)])
            r = self.run_act(c)
            self.assertEqual(len(c.calls), 2)
            self.assertEqual(r['first_pass_decision']['level'], 'IGNORE')
            self.assertEqual(r['decision'], decision(lev))
            self.assertTrue(r['verification']['completed'])
            self.assertEqual([x['round'] for x in r['rounds']], [1, 2])

    def test_review_can_confirm_ignore_without_third_call(self):
        c = Client([decision('IGNORE'), decision('IGNORE', 'No business connection.')])
        r = self.run_act(c)
        self.assertEqual(len(c.calls), 2)
        self.assertEqual(r['decision']['level'], 'IGNORE')
        self.assertEqual(r['decision']['reason'], 'No business connection.')

    def test_same_full_payload_resent_without_first_reason_or_labels(self):
        c = Client([decision('IGNORE', 'OLD_REASON_SECRET'), decision('INDIRECT')])
        r = self.run_act(c)
        first = json.loads(c.calls[0][0][1]['content'])
        second = json.loads(c.calls[1][0][1]['content'])
        self.assertEqual(first, second)
        self.assertEqual(second['business_profile'], PROFILE)
        self.assertEqual(second['title'], act()['document']['title_en'])
        self.assertNotIn('OLD_REASON_SECRET', json.dumps(c.calls[1][0]))
        self.assertTrue(r['rounds'][1]['same_evidence_as_first_pass'])
        self.assertEqual(set(r['decision']), {'reason', 'anchor_ids', 'level'})

    def test_review_failure_does_not_silently_keep_first_ignore(self):
        c = Client([decision('IGNORE'), RuntimeError('Network unavailable')])
        r = self.run_act(c)
        self.assertEqual(r['status'], 'failed')
        self.assertNotIn('decision', r)
        self.assertEqual(r['first_pass_decision']['level'], 'IGNORE')
        self.assertFalse(r['verification']['completed'])
        self.assertEqual(len(c.calls), 2)

    def test_invalid_review_format_is_a_technical_failure(self):
        c = Client([decision('IGNORE'), {'level': 'DIRECT'}])
        self.assertEqual(self.run_act(c)['status'], 'failed')

    def test_cut_off_review_is_not_treated_as_a_completed_ignore(self):
        class CutOffClient(Client):
            def chat(self, messages, **kwargs):
                result = super().chat(messages, **kwargs)
                if len(self.calls) == 2:
                    result.finish_reason = 'length'
                return result
        c = CutOffClient([decision('IGNORE'), decision('IGNORE')])
        result = self.run_act(c)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['first_pass_decision']['level'], 'IGNORE')
        self.assertFalse(result['verification']['completed'])

    def test_initial_failure_does_not_trigger_review(self):
        c = Client([RuntimeError('Network unavailable')])
        r = self.run_act(c)
        self.assertEqual(r['status'], 'failed')
        self.assertIsNone(r['first_pass_decision'])
        self.assertEqual(len(c.calls), 1)

    def test_ablation_disables_second_call(self):
        c = Client([decision('IGNORE')])
        r = self.run_act(c, review_ignore=False)
        self.assertEqual(len(c.calls), 1)
        self.assertEqual(r['decision']['level'], 'IGNORE')
        self.assertFalse(r['verification']['enabled'])

    def test_both_prompts_fit_budget_and_review_sees_new_evidence(self):
        c = Client([decision('IGNORE'), decision('IGNORE')], limited=True)
        r = self.run_act(c, input_budget=1000)
        self.assertEqual(r['status'], 'ok')
        self.assertTrue(r['rounds'][1]['new_source_ids'])
        self.assertEqual(r['rounds'][0]['selected_source_ids'][:4],r['rounds'][1]['selected_source_ids'][:4])
        self.assertLessEqual(r['rounds'][1]['input_tokens'], 1000)
        self.assertEqual(len(r['evidence']), 8)

    def test_status_metadata_preserved_without_guessing(self):
        a = act()
        self.assertFalse(f.legal_status_metadata(a)['available'])
        a['document']['is_in_force'] = False
        a['instrument']['repealed_by_celex'] = '32026R0001'
        result = f.legal_status_metadata(a)
        self.assertIs(result['fields']['document.is_in_force'], False)
        self.assertEqual(result['fields']['instrument.repealed_by_celex'], '32026R0001')
        self.assertFalse(result['independently_verified'])

    def test_profile_clarifications_preserve_yaml_ids_exclusions_and_not_roles_string(self):
        import yaml
        # Exercise the real profile function without importing the office-only
        # client and catalogue dependencies needed by the command-line runner.
        tree = ast.parse((ROOT / 'tools/document_relevance.py').read_text())
        node = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == 'profile')
        namespace = {'yaml': yaml, 'digest': lambda raw: hashlib.sha256(raw).hexdigest()}
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<profile>', 'exec'), namespace)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = {'roles': {'taxpayer': 'Taxpayer'}, 'not_roles': 'Not an electrical manufacturer.',
                      'levels': {}, 'negative_anchors': {'agriculture': {'text': 'Excluded'}},
                      'indirect_paths': {'suppliers_prices': 'Energy and material supply.'},
                      'anchors': {key: {'label': key, 'scope': 'Existing scope', 'domain': 'd',
                                        'roles': ['taxpayer'], 'not_covered': 'Excluded categories'}
                                  for key in ('finance_tax', 'fleet_commercial_vehicles', 'energy_efficiency_operations')}}
            path = root / 'anchors.yaml'
            path.write_text(yaml.safe_dump(source))
            domains = root / 'domains.yaml'
            domains.write_text('domains: {d: {}}')
            before = path.read_bytes()
            result, digest = namespace['profile'](path, domains)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(digest, hashlib.sha256(before).hexdigest())
            self.assertEqual(result['not_roles'], source['not_roles'])
            self.assertEqual(set(result['anchors']), set(source['anchors']))
            self.assertEqual(result['negative_anchors'], source['negative_anchors'])
            self.assertEqual(result['anchors']['finance_tax']['scope'], 'Existing scope')
            self.assertEqual(result['anchors']['fleet_commercial_vehicles']['not_covered'], 'Excluded categories')
            self.assertIn('capital', result['anchors']['finance_tax']['note'])


if __name__ == '__main__':
    unittest.main()
