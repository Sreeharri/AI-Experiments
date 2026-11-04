"""v4 rules: short answers, cut-off answers get a second look, roles are facts, parallel runs."""
from datetime import date
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from lr_ai import document_filter as f

PROFILE = {"roles": {"operator": "plant operator"},
           "anchors": {"plants": {"roles": ["operator"], "scope": "industrial plant operation"}},
           "indirect_paths": {"purchased_products": "supplier product impact"}}


def act():
    return {"celex": "32024R3110", "document": {"title_en": "Plant rules"}, "instrument": {},
            "version": {"id": "v1"},
            "elements": [{"element_type": "ARTICLE", "path": "article/1", "heading": "Scope",
                          "body_text": "Operators shall register.", "details": {"aggregate_body": True}}]}


def decision():
    return {"screening_basis": "role_match", "level": "DIRECT", "reason": "Operator duty", "affected_party": "operator",
            "company_role_ids": ["operator"], "anchor_ids": ["plants"], "indirect_path_id": None,
            "evidence": [{"source_id": "article/1#chunk1", "quote": "Operators shall register."}],
            "unresolved_conditions": [], "requested_source_ids": [], "search_queries": [],
            "applicability_basis": "current"}


class Reply:
    def __init__(self, value=None, truncated=False, content=""):
        self.value, self.truncated, self.content, self.ok = value, truncated, content, True

    def parse_json(self):
        return (None, "answer cut off") if self.truncated else (self.value, None)


class Client:
    def __init__(self, replies):
        self.replies, self.calls, self.max_tokens, self.thinking = iter(replies), 0, [], []

    def tokenize(self, **kwargs):
        return 100

    def clean(self, value):
        return value

    def chat(self, messages, **kwargs):
        self.calls += 1
        self.max_tokens.append(kwargs["max_tokens"])
        self.thinking.append(kwargs["thinking"])
        return next(self.replies)


def run(client, value=None):
    return f.run_act(client, value or act(), PROFILE, date(2026, 11, 4), 40960)


class V4Tests(unittest.TestCase):
    def test_cut_off_answer_gets_one_shorter_second_look(self):
        c = Client([Reply(truncated=True, content='{"anchor_ids": ["a", "b", "c", ' * 50), Reply(decision())])
        r = run(c)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(c.calls, 2)
        self.assertEqual(c.thinking, [False, False])
        self.assertEqual(c.max_tokens, [4096, 6000])
        self.assertIn("cut off", r["rounds"][0]["validation_error"])
        self.assertTrue(r["rounds"][0]["cut_off_answer_preview"].startswith('{"anchor_ids"'))

    def test_twice_cut_off_is_review_not_failure(self):
        c = Client([Reply(truncated=True), Reply(truncated=True)])
        r = run(c)
        self.assertEqual(r["status"], "needs_review")
        self.assertEqual(c.calls, 2)

    def test_too_many_anchors_is_rejected_and_retried(self):
        many = decision()
        many["anchor_ids"] = ["plants"] * 46
        c = Client([Reply(many), Reply(decision())])
        r = run(c)
        self.assertEqual(r["status"], "ok")
        self.assertIn("anchor_ids", r["rounds"][0]["validation_error"])

    def test_long_quote_is_rejected(self):
        d = decision()
        d["evidence"][0]["quote"] = "x" * (f.MAX_QUOTE_CHARS + 1)
        blocks, _, _ = f.prepare(act(), date(2026, 11, 4))
        with self.assertRaises(Exception):
            f.check_decision(d, blocks, PROFILE)

    def test_prompt_states_roles_are_facts(self):
        self.assertIn("Accept supplied company roles WITHIN", f.DECISION_PROMPT)
        self.assertIn("at most 3 anchor IDs", f.DECISION_PROMPT)

    def test_parallel_runner_writes_every_act(self):
        import importlib.util
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("document_relevance", root / "tools/document_relevance.py")
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data, out = tmp / "data", tmp / "out"
            data.mkdir()
            ids = [f"3202{i}R000{i}" for i in range(1, 7)]
            (data / "acts.jsonl").write_text("".join(json.dumps(
                {"celex": c, "act_type": "regulation", "title": {"en": f"Rules {c}", "de": None}}) + "\n" for c in ids))
            (data / "text_en.jsonl").write_text("".join(json.dumps(
                {"celex": c, "language": "EN", "version": {"key": c},
                 "articles": [{"n": "1", "heading": "Article 1 / Scope", "text": "Operators shall register."}]}) + "\n"
                for c in ids))

            class Fake(Client):
                model, api_base, max_model_len = "fake", "https://fake.invalid/v1", 40960

                def discover(self):
                    return {"ok": True}

                def chat(self, messages, **kwargs):
                    d = decision()
                    d.update(anchor_ids=["industrial_emissions"], company_role_ids=["plant_operator"])
                    d["evidence"][0]["source_id"] = json.loads(messages[1]["content"])["excerpts"][0]["source_id"]
                    return Reply(d)

            fake = Fake([])
            argv = ["document_relevance.py", "--input", str(data), "--out", str(out), "--workers", "3",
                    "--anchors", str(root / "config/business_anchors_en.yaml"),
                    "--domains", str(root / "config/domains.yaml"), "--env", str(tmp / ".env")]
            with patch.object(runner.QwenClient, "from_env", return_value=fake), patch.object(sys, "argv", argv):
                self.assertEqual(runner.main(), 0)
            summary = json.loads((out / "summary.json").read_text())
            self.assertEqual(summary["acts"], 6)
            self.assertEqual(summary["workers"], 3)
            self.assertEqual(summary["counts"], {"DIRECT": 6})
            self.assertEqual(sorted(p.stem for p in out.glob("3*.json")), sorted(ids))


if __name__ == "__main__":
    unittest.main()
