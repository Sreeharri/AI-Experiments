from datetime import date
from types import SimpleNamespace
import unittest
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
    return {"level": "DIRECT", "reason": "Operator duty", "affected_party": "operator",
            "company_role_ids": ["operator"], "anchor_ids": ["plants"], "indirect_path_id": None,
            "evidence": [{"source_id": "article/1#chunk1", "quote": "Operators shall register."}],
            "unresolved_conditions": [], "requested_source_ids": [], "search_queries": [],
            "applicability_basis": "current"}

class Client:
    def __init__(self, replies):
        self.replies, self.calls = iter(replies), 0
    def tokenize(self, **kwargs):
        return 100
    def clean(self, value):
        return value
    def chat(self, messages, **kwargs):
        self.calls += 1
        result = next(self.replies)
        return SimpleNamespace(parse_json=lambda: (result, None))

class Tests(unittest.TestCase):
    def run_one(self, client, value=None, **kwargs):
        return f.run_act(client, value or act(), PROFILE, date(2026,11,4), 40960, **kwargs)
    def test_quotes_and_anchor_ids(self):
        blocks, _, _ = f.prepare(act(), date(2026,11,4))
        f.check_decision(decision(), blocks, PROFILE)
        for change in ({"anchor_ids": ["invalid"]}, {"evidence": [{"source_id": "article/1#chunk1", "quote": "Invented"}]},
                       {"unresolved_conditions": ["Unknown equipment capacity"]}):
            with self.subTest(change=change):
                d=decision()
                d.update(change)
                with self.assertRaises(ValueError):
                    f.check_decision(d, blocks, PROFILE)
    def test_supported(self):
        c=Client([decision(), {"supported": True, "issues": [], "requested_source_ids": [], "search_queries": []}])
        self.assertEqual(self.run_one(c)["decision"]["level"], "DIRECT")
        self.assertEqual(c.calls, 2)
    def test_rejected(self):
        c=Client([decision(), {"supported": False, "issues": ["Unverified capacity"], "requested_source_ids": [], "search_queries": []}])
        self.assertEqual(self.run_one(c)["decision"]["level"], "NEEDS_REVIEW")
    def test_future_and_bad_boundaries(self):
        for change in ("future", "duplicate"):
            a=act()
            if change=="future":
                a["version"]["version_date"]="2027-01-01"
            else:
                a["elements"][0]["path"]="article/1~duplicate-2"
            c=Client([])
            self.assertEqual(self.run_one(c,a)["decision"]["level"],"NEEDS_REVIEW")
            self.assertEqual(c.calls,0)
    def test_ignore_coverage(self):
        a=act()
        for i in range(2,13):
            a["elements"].append({"element_type": "ARTICLE","path": f"article/{i}","heading": "",
                                 "body_text": f"Other provision {i}.", "details": {"aggregate_body":True}})
        d=decision()
        d.update(level="IGNORE",anchor_ids=[],company_role_ids=[],applicability_basis="not_applicable")
        c=Client([d,{"supported":True,"issues":[],"requested_source_ids":[],"search_queries":[]}])
        r=self.run_one(c,a,max_rounds=1)
        self.assertEqual(r["decision"]["level"],"NEEDS_REVIEW")
        self.assertFalse(r["coverage"]["all_operative_chunks_seen"])
    def test_chunks_preserve_text(self):
        text="First sentence. "*1000
        chunks=list(f.split_text(text))
        self.assertEqual("".join(chunks),text.strip())
        self.assertTrue(all(len(s)<=2800 for s in chunks))
    def test_loop_adds_evidence(self):
        a=act()
        for i in range(2,11):
            a["elements"].append({"element_type":"ARTICLE","path":f"article/{i}","heading":"",
                                 "body_text":f"Additional duty {i}.","details":{"aggregate_body":True}})
        first=decision()
        first["requested_source_ids"]=["article/9#chunk1"]
        critic={"supported":False,"issues":["Read missing source"],"requested_source_ids":["article/9#chunk1"],"search_queries":[]}
        c=Client([first,critic,decision(),{**critic,"supported":True,"issues":[],"requested_source_ids":[]}])
        r=self.run_one(c,a)
        self.assertEqual(r["decision"]["level"],"DIRECT")
        self.assertEqual(c.calls,4)
        self.assertGreater(len(r["rounds"][1]["selected_source_ids"]),len(r["rounds"][0]["selected_source_ids"]))

if __name__=="__main__":
    unittest.main()
