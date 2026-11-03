"""Tests for tools/export_eurlex.py that need no database.

Run:  py -m pytest tests -q      (or:  py tests\\test_export_eurlex.py)
The notice fixture is the real CELLAR branch notice of Directive (EU) 2024/1785,
shortened to the fields the export reads.
"""
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

import export_eurlex as ex  # noqa: E402
from lr_ai.eurlex_data import TextStore, load_acts  # noqa: E402

NOTICE = (ROOT / 'tests' / 'fixtures' / 'notice_32024L1785_en.xml').read_bytes()


def test_real_notice_fields():
    n = ex.parse_notice(NOTICE)
    ids = {x['id'] for x in n['eurovoc']}
    assert '1158' in ids and len(ids) >= 10                      # waste management + others
    assert {x['id'] for x in n['eurovoc_domains']} == {'52'}
    assert n['eurovoc_microthesauri']
    assert any(x['id'] == '15103030' and x['label'].startswith('Environment, consumers and health protection > ')
               for x in n['directory_codes'])
    assert {x['id'] for x in n['subject_matters']} >= {'DECH'}
    assert [x['id'] for x in n['addressees']] == ['EUMS']
    assert {x['id'] for x in n['authors']} == {'CONSIL', 'EP'}   # CREATED_BY duplicates are merged
    assert n['responsible_dg'][0]['id'] == 'ENV'
    assert n['resource_type'][0]['label'] == 'Directive'
    assert n['legal_basis'] == [{'celex': '12016E192', 'detail': {'PARAGRAPH': '1'}, 'label': 'TFEU Art. 192(1)'}]
    assert n['transposition_dates'] == ['2026-07-01']


def test_notice_with_namespace_and_dtd():
    xml = (b'<NOTICE xmlns="http://x"><WORK><WORK_IS_ABOUT_CONCEPT_EUROVOC type="concept"><IDENTIFIER>7</IDENTIFIER>'
           b'<PREFLABEL>concrete</PREFLABEL></WORK_IS_ABOUT_CONCEPT_EUROVOC></WORK></NOTICE>')
    assert ex.parse_notice(xml)['eurovoc'] == [{'id': '7', 'label': 'concrete'}]
    try:
        ex.parse_notice(b'<!DOCTYPE x [<!ENTITY a "b">]><NOTICE/>')
        raise AssertionError('DTD accepted')
    except ValueError:
        pass
    assert ex.parse_notice(b'<OTHER/>')['eurovoc'] == []


def test_merge_labels_and_treaty_labels():
    merged = ex.merge_labels([{'id': '1', 'label': 'waste'}], [{'id': '1', 'label': 'Abfall'}, {'id': '2', 'label': 'Bau'}])
    assert merged == [{'id': '1', 'en': 'waste', 'de': 'Abfall'}, {'id': '2', 'en': None, 'de': 'Bau'}]
    assert ex.treaty_label('12016E114', {}) == 'TFEU Art. 114'
    assert ex.treaty_label('11997E095', {'PARAGRAPH': '1'}) == 'EC Treaty Art. 95(1)'
    assert ex.treaty_label('12016M003', {}) == 'TEU Art. 3'
    assert ex.treaty_label('32011R0305', {}) is None


def version(**kw):
    base = dict(id='v', language='EN', version_key='k', version_kind='ORIGINAL', version_date=None, is_current=True,
                is_preferred=False, quality_status='VALIDATED', quality_score=None, source_format='XHTML',
                fetched_at=dt.datetime(2026, 1, 1), recital_count=0)
    base.update(kw)
    return base


def test_choose_versions_prefers_preferred_and_takes_recitals_from_original():
    cons = version(id='c', version_kind='CONSOLIDATED', is_preferred=True)
    orig = version(id='o', recital_count=12, is_current=False)
    de = version(id='d', language='DE', recital_count=3)
    chosen = ex.choose_versions([orig, cons, de])
    assert chosen['EN'][0]['id'] == 'c' and chosen['EN'][1]['id'] == 'o'
    assert chosen['DE'][0]['id'] == 'd' and chosen['DE'][1]['id'] == 'd'
    assert ex.choose_versions([version(id='x')])['EN'][1] is None


def element(i, kind, parent=None, number=None, heading=None, text=''):
    return dict(id=i, parent_id=parent, element_type=kind, number=number, identifier=f'{kind} {number}',
                heading=heading, body_text=text)


def test_build_text():
    elements = [element('c1', 'CHAPTER', number='I', heading='CHAPTER I / General'),
                element('a1', 'ARTICLE', 'c1', '1', 'Article 1 / Scope', 'Article 1 Scope It applies.'),
                element('x1', 'ANNEX', None, 'I', 'ANNEX I', 'A' * 500),
                element('a9', 'ARTICLE', 'x1', '9', 'Article 9', 'inside annex'),
                element('p1', 'APPENDIX', 'x1', '1', 'Appendix 1', 'inside annex too')]
    outline, articles, annexes, document_text = ex.build_text(elements, 100)
    assert outline == [{'type': 'CHAPTER', 'number': 'I', 'heading': 'CHAPTER I / General'}]
    assert articles == [{'n': '1', 'heading': 'Article 1 / Scope', 'within': 'CHAPTER I / General',
                         'text': 'Article 1 Scope It applies.'}]
    assert len(annexes) == 1 and annexes[0]['truncated'] and annexes[0]['chars'] == 500 and len(annexes[0]['text']) == 100
    assert document_text is None
    assert ex.build_text([element('d', 'DOCUMENT_TEXT', text='full text')], 0)[3] == 'full text'


def test_recitals_from_json_or_combined_text():
    assert ex.recitals_from([{'number': '1', 'text': 'a'}], '') == [{'n': '1', 'text': 'a'}]
    assert ex.recitals_from([], '(1) first\n\n(2) second') == [{'n': '1', 'text': 'first'}, {'n': '2', 'text': 'second'}]


def test_population_patterns():
    assert ex.ACT_CELEX.match('32024R3110') and ex.ACT_CELEX.match('32020D0123(01)')
    assert not ex.ACT_CELEX.match('32024R3110R(02)') and not ex.ACT_CELEX.match('02011R0305-20210716')
    assert ex.CORRIGENDUM_CELEX.search('32024R3110R(02)')


def test_text_store_reads_lines_by_celex():
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / 'acts.jsonl').write_text(json.dumps({'celex': '1', 'title': {'en': 'x'}}) + '\n', encoding='utf-8')
        lines = [json.dumps({'celex': c, 'language': 'EN', 'articles': [{'text': 'Ä' * i}]}, ensure_ascii=False)
                 for i, c in enumerate(['32011R0305', '32024R3110', '32010L0075'])]
        (folder / 'text_en.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        assert load_acts(folder)['1']['title']['en'] == 'x'
        store = TextStore('EN', folder)
        assert store.get('32024R3110')['articles'][0]['text'] == 'Ä'
        assert store.get('32010L0075')['celex'] == '32010L0075'
        assert '32011R0305' in store and store.get('nope') is None
        assert TextStore('EN', folder).get('32011R0305')['celex'] == '32011R0305'   # saved index reused


if __name__ == '__main__':
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for test in tests:
        test()
    print(f'{len(tests)} tests passed')
