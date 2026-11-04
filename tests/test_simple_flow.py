from copy import deepcopy
from datetime import date
import importlib.util
import json
from pathlib import Path
import unittest

from lr_ai import document_filter as f

PROFILE = {'roles': {'operator': 'plant operator'}, 'anchors': {'plant': {'roles': ['operator'], 'scope': 'plant operation'}},
           'not_roles': {'bank': 'not a bank'}, 'indirect_paths': {}, 'negative_anchors': {}}


def act():
    return {'celex': '32024R3110', 'document': {'title_en': 'Plant rules'}, 'instrument': {},
            'version': {'id': 'v1'}, 'elements': [
                {'path': f'article/{i}', 'element_type': 'ARTICLE', 'heading': 'Scope' if i == 1 else 'Provision',
                 'number': str(i), 'body_text': f'Operators use equipment item {i}.', 'details': {'aggregate_body': True}}
                for i in range(1, 20)]}


def model_decision(level):
    return {'reason': f'Model explanation for {level}.',
            'anchor_ids': ['plant'] if level in ('DIRECT', 'INDIRECT') else [],
            'level': level}


class Reply:
    ok = True
    finish_reason = 'stop'
    def __init__(self, level, error=None):
        self.value = model_decision(level)
        self.content = json.dumps(self.value)
        self.error = error
    def parse_json(self):
        return (None, self.error) if self.error else (self.value, None)


class Client:
    def __init__(self, replies):
        self.replies, self.calls = iter(replies), []
    def tokenize(self, **kw):
        return 100
    def chat(self, messages, **kw):
        self.calls.append({'payload': json.loads(messages[1]['content']), 'options': kw})
        return next(self.replies)
    def clean(self, text):
        return text


def run(c, value=None):
    return f.run_act(c, value or act(), PROFILE, date(2026, 11, 4), 40960)


class SimpleFlowTests(unittest.TestCase):
    def test_direct_indirect_ignore_are_saved_immediately(self):
        for label in ('DIRECT', 'INDIRECT', 'IGNORE'):
            with self.subTest(label=label):
                c = Client([Reply(label)])
                result = run(c)
                self.assertEqual(result['decision'], model_decision(label))
                self.assertEqual(len(c.calls), 1)

    def test_review_then_resolved(self):
        for label in ('DIRECT', 'INDIRECT', 'IGNORE'):
            c = Client([Reply('NEEDS_REVIEW'), Reply(label)])
            result = run(c)
            self.assertEqual(result['decision'], model_decision(label))
            self.assertEqual(len(c.calls), 2)
            self.assertEqual(c.calls[1]['payload']['previous_decision'], model_decision('NEEDS_REVIEW'))

    def test_review_twice_is_saved_without_third_call(self):
        c = Client([Reply('NEEDS_REVIEW'), Reply('NEEDS_REVIEW')])
        result = run(c)
        self.assertEqual(result['decision'], model_decision('NEEDS_REVIEW'))
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(len(c.calls), 2)

    def test_second_pass_receives_extra_available_passages(self):
        c = Client([Reply('NEEDS_REVIEW'), Reply('DIRECT')])
        run(c)
        first = c.calls[0]['payload']['excerpts']
        second = c.calls[1]['payload']['excerpts']
        self.assertGreater(len(second), len(first))

    def test_missing_text_does_not_override_qwen(self):
        value = act(); value['elements'] = []
        c = Client([Reply('DIRECT')])
        result = run(c, value)
        self.assertEqual(result['decision'], model_decision('DIRECT'))
        self.assertEqual(len(c.calls), 1)

    def test_future_version_does_not_override_qwen(self):
        value = act(); value['version']['version_date'] = '2027-01-01'
        c = Client([Reply('IGNORE')])
        result = run(c, value)
        self.assertEqual(result['decision'], model_decision('IGNORE'))
        self.assertTrue(result['version_assessment']['future_version'])

    def test_duplicate_and_rejected_data_do_not_override_qwen(self):
        value = act(); value['elements'][0]['path'] = 'article/1~duplicate2'
        value['version']['quality_status'] = 'REJECTED'
        c = Client([Reply('DIRECT')])
        result = run(c, value)
        self.assertEqual(result['decision'], model_decision('DIRECT'))

    def test_no_quote_anchor_or_role_checks(self):
        self.assertEqual(list(f.SCHEMA['properties']), ['reason', 'anchor_ids', 'level'])
        self.assertFalse(hasattr(f, 'check_decision'))
        self.assertFalse(hasattr(f, 'REVIEW_PROMPT'))

    def test_protocol_error_is_failure_not_review_and_no_second_call(self):
        c = Client([Reply(None, error='answer cut off')])
        result = run(c)
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('decision', result)
        self.assertEqual(len(c.calls), 1)
        self.assertTrue(result['rounds'][0]['raw_content'])

    def test_not_roles_reaches_model(self):
        c = Client([Reply('DIRECT')]); run(c)
        self.assertEqual(c.calls[0]['payload']['business_profile']['not_roles'], PROFILE['not_roles'])

    def test_profile_loader_preserves_not_roles(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('runner', root/'tools/document_relevance.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        value, _ = module.profile(root/'config/business_anchors_en.yaml', root/'config/domains.yaml')
        import yaml
        source = yaml.safe_load((root/'config/business_anchors_en.yaml').read_text())
        self.assertEqual(value['not_roles'], source.get('not_roles', {}))
        self.assertEqual(value['valid_anchor_ids'], list(source['anchors']))
        self.assertIn('chemicals_clp', value['valid_anchor_ids'])
        self.assertNotIn('chemicals_user', value['valid_anchor_ids'])

    def test_profile_keeps_explicit_machinery_note_for_qwen(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('runner', root/'tools/document_relevance.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        value, _ = module.profile(root/'config/business_anchors_en.yaml', root/'config/domains.yaml')
        self.assertIn('INDIRECT', value['anchors']['machinery_work_equipment']['note'])

    def test_both_passes_request_thinking_and_6000_tokens(self):
        c = Client([Reply('NEEDS_REVIEW'), Reply('DIRECT')]); run(c)
        self.assertEqual([x['options']['thinking'] for x in c.calls], [True, True])
        self.assertEqual([x['options']['max_tokens'] for x in c.calls], [6000, 6000])

    def test_reason_and_anchors_are_preserved_exactly(self):
        reply = Reply('DIRECT')
        reply.value.update(reason='Conditional model reasoning, copied without judgement.',
                           anchor_ids=['an_id_chosen_by_qwen'])
        c = Client([reply])
        result = run(c)
        self.assertEqual(result['decision'], reply.value)
        self.assertEqual(len(c.calls), 1)

    def test_second_pass_reason_and_anchors_are_saved(self):
        first, second = Reply('NEEDS_REVIEW'), Reply('INDIRECT')
        second.value.update(reason='Supplier requirements affect purchased equipment.', anchor_ids=['equipment'])
        result = run(Client([first, second]))
        self.assertEqual(result['decision'], second.value)
        self.assertEqual(result['rounds'][0]['candidate'], first.value)

    def test_ignore_keeps_empty_anchor_list(self):
        result = run(Client([Reply('IGNORE')]))
        self.assertEqual(result['decision']['anchor_ids'], [])
        self.assertTrue(result['decision']['reason'])

    def test_inline_notes_do_not_change_initial_passage_ranking(self):
        compact = deepcopy(PROFILE)
        inline = deepcopy(PROFILE)
        inline['anchors']['plant'].update(note='New words must not alter retrieval.', not_covered='Different product')
        blocks, _, _ = f.prepare(act(), date(2026, 11, 4))
        self.assertEqual(f.initial_selection(blocks, compact), f.initial_selection(blocks, inline))

    def test_profile_keeps_all_notes_inline_and_full_company_summary(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('runner', root/'tools/document_relevance.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        value, _ = module.profile(root/'config/business_anchors_en.yaml', root/'config/domains.yaml')
        import yaml
        original = yaml.safe_load((root/'config/business_anchors_en.yaml').read_text())
        for key, anchor in original['anchors'].items():
            for field in ('scope', 'roles', 'note', 'not_covered'):
                if field in anchor:
                    self.assertEqual(value['anchors'][key][field], anchor[field])
        self.assertNotIn('anchor_caveats', value)
        self.assertIn('storage', value['company_summary'])
        self.assertIn('wind/solar', value['company_summary'])

    def test_real_http_client_serializes_first_pass_thinking(self):
        from lr_ai.qwen_client import QwenClient
        c = Client([Reply('DIRECT')]); run(c)
        payload = QwenClient('https://example.invalid', model='test').build_payload(
            [{'role': 'user', 'content': 'test'}], **c.calls[0]['options'])
        self.assertTrue(payload['chat_template_kwargs']['enable_thinking'])
        self.assertEqual(payload['max_tokens'], 6000)


if __name__ == '__main__':
    unittest.main()
