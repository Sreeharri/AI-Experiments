import unittest

from lxml import etree

from eurlex_enrichment.references import Resolver
from eurlex_enrichment.xhtml import table_grid


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.resolver = Resolver("32024R3110", {
            "annex": {"id": "annex", "kind": "ANNEX", "number": "II", "path": "annex/ii"},
            "art11": {"id": "art11", "kind": "ARTICLE", "number": "11", "path": "article/11"},
        }, [
            {"id": "one", "annex_id": "annex", "part": "1", "published_number": "1"},
            {"id": "two", "annex_id": "annex", "part": "2", "published_number": "1"},
        ])

    def test_repeated_table_number_is_ambiguous_without_part(self):
        refs = list(self.resolver.links("a", "Apply Table 1 of Annex II."))
        self.assertEqual(refs[0]["status"], "UNRESOLVED")
        self.assertEqual(refs[0]["candidate_ids"], ["one", "two"])
        self.assertIsNone(refs[0]["target_id"])

    def test_part_disambiguates_published_table_number(self):
        refs = list(self.resolver.links("a", "Apply Table 1 of Part 2 of Annex II."))
        self.assertEqual(refs[0]["target_id"], "two")

    def test_external_article_does_not_resolve_to_local_article(self):
        refs = list(self.resolver.links(
            "a", "Subject to Directive (EU) 2020/2184 and Article 11(8) of that Directive."
        ))
        self.assertEqual(refs[0]["target_instrument"], "32020L2184")
        self.assertEqual(refs[0]["status"], "EXTERNAL_TEXT_REQUIRED")
        self.assertIsNone(refs[0]["target_id"])


class GridTests(unittest.TestCase):
    def test_spans_preserve_cell_coordinates(self):
        table = etree.fromstring(b"""<table xmlns="http://www.w3.org/1999/xhtml">
            <tbody><tr><th rowspan="2">A</th><th colspan="2">B</th></tr>
            <tr><td>42 <a href="#ntr1">(1)</a></td><td>43</td></tr></tbody>
        </table>""")
        rows = table_grid(table)
        self.assertEqual(rows[0]["cells"][1]["colspan"], 2)
        self.assertEqual(rows[1]["cells"][0]["column"], 2)
        self.assertEqual(rows[1]["cells"][0]["footnote_ids"], ["ntr1"])


if __name__ == "__main__":
    unittest.main()
