from copy import deepcopy
from datetime import date
import json
import unittest

from lr_ai import document_filter as f
from test_v4_rules import act, decision, PROFILE, Client, Reply, run


class RegressionTests(unittest.TestCase):
    def test_bad_boundaries_reach_ai_and_preserve_warning(self):
        value = act()
        extra = deepcopy(value['elements'][0])
        extra.update(path='article/1~duplicate2', body_text='More operator requirements.')
        value['elements'].append(extra)
        client = Client([Reply(decision())])
        result = run(client, value)
        self.assertEqual(client.calls, 1)
        self.assertEqual(result['status'], 'ok')
        self.assertTrue(result['data_warnings'])
        self.assertFalse(result['evidence'][0]['citation_verified'])

    def test_future_version_reaches_ai_but_cannot_be_current(self):
        value = act()
        value['version'].update(version_date='2027-01-01', effective_from='2027-01-01')
        client = Client([Reply(decision())])
        result = run(client, value)
        self.assertEqual(client.calls, 1)
        self.assertEqual(result['decision']['applicability_basis'], 'future_or_transposition')
        self.assertEqual(result['version_assessment']['assessment_scope'], 'potential_future_relevance')
        self.assertTrue(result['data_warnings'])

    def test_missing_text_reaches_ai_and_stays_review(self):
        value = act()
        value['elements'] = []
        client = Client([Reply(f.review_result('No legal evidence'))])
        result = run(client, value)
        self.assertEqual(client.calls, 1)
        self.assertEqual(result['status'], 'needs_review')

    def test_rejected_source_reaches_ai_but_is_not_accepted(self):
        value = act()
        value['version']['quality_status'] = 'REJECTED'
        client = Client([Reply(decision())])
        result = run(client, value)
        self.assertEqual(client.calls, 1)
        self.assertEqual(result['status'], 'needs_review')

    def test_same_text_in_distinct_articles_is_not_discarded(self):
        value = act()
        second = deepcopy(value['elements'][0])
        second.update(path='article/2', number='2')
        value['elements'].append(second)
        blocks, _, _ = f.prepare(value, date(2026, 11, 4))
        self.assertEqual(len(blocks), 2)

    def test_output_retry_uses_same_passages_and_larger_budget(self):
        class Recording(Client):
            def chat(self, messages, **kw):
                self.payloads.append(json.loads(messages[1]['content']))
                return super().chat(messages, **kw)
        client = Recording([Reply(truncated=True, content='{"reason":'), Reply(decision())])
        client.payloads = []
        result = run(client)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(client.payloads[0]['excerpts'], client.payloads[1]['excerpts'])
        self.assertEqual(client.max_tokens, [4096, 6000])
        self.assertIn('input_tokens', result['rounds'][0])

    def test_long_review_reason_still_meets_schema(self):
        f.validate(f.review_result('error ' * 1000), f.SCHEMA)

    def test_importer_assumption_not_promoted_to_fact(self):
        self.assertIn('unconfirmed_assumption takes precedence', f.DECISION_PROMPT)


if __name__ == '__main__':
    unittest.main()
