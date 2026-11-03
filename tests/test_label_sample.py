"""Tests for tools/make_label_sample.py and tools/check_labels.py (no database needed).

Run:  py tests\\test_label_sample.py
"""
import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

import make_label_sample as ms  # noqa: E402
from lr_ai.eurlex_data import domain_codes, label  # noqa: E402
from openpyxl import load_workbook  # noqa: E402


def fake_export(folder, kept=900, blocked=600):
    rng = random.Random(3)
    domains = [('20', '20 TRADE'), ('56', '56 AGRICULTURE'), ('52', '52 ENVIRONMENT'), ('66', '66 ENERGY'), ('72', '72 GEOGRAPHY')]
    acts, texts = [], []
    for i in range(kept + blocked):
        kind = rng.choices('RLD', [46, 6, 48])[0]
        celex = f'3{1990 + i // 10000}{kind}{i % 10000:04d}'
        act = {'celex': celex, 'act_type': {'R': 'regulation', 'L': 'directive', 'D': 'decision'}[kind],
               'title': {'en': f'Title {i}', 'de': f'Titel {i}'}, 'filter': {'decision': 'BLOCK' if i >= kept else 'KEEP'},
               'eurovoc': [{'id': '1', 'en': 'building industry', 'de': 'Bauindustrie'}], 'eurovoc_domains': [],
               'legal_basis': [{'celex': '12016E114', 'label': 'TFEU Art. 114', 'detail': {}}],
               'family': {'changes': [], 'changes_count': 0}}
        if i < kept:
            act['eurovoc_domains'] = [{'id': d, 'en': name, 'de': None} for d, name in rng.sample(domains, 2)]
            texts.append({'celex': celex, 'articles': [{'n': '1', 'text': 'Article 1 This Regulation applies to cranes.'}]})
        acts.append(act)
    (folder / 'acts.jsonl').write_text(''.join(json.dumps(a) + '\n' for a in acts), encoding='utf-8')
    (folder / 'text_en.jsonl').write_text(''.join(json.dumps(t) + '\n' for t in texts), encoding='utf-8')


def run(*args):
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, cwd=ROOT)


def test_domain_codes_and_labels():
    act = {'eurovoc_domains': [{'id': '100147', 'en': '', 'de': None}, {'id': '52', 'en': '52 ENVIRONMENT'},
                               {'id': '66', 'en': None, 'de': '66 ENERGIE'}]}
    assert domain_codes(act) == {'20', '52', '66'}
    assert label({'id': '1', 'en': None, 'de': 'Abfall'}) == 'Abfall'
    assert ms.group_of({'filter': {'decision': 'BLOCK'}}) == 'blocked'
    assert ms.group_of({'filter': {'decision': 'KEEP'}, 'eurovoc_domains': [{'id': '100147', 'en': ''}]}) == 'far'
    assert ms.group_of({'filter': {'decision': 'KEEP'}, 'eurovoc_domains': []}) == 'near'


def test_sqrt_allocation():
    alloc = ms.sqrt_allocation({'regulation': 2000, 'directive': 200, 'decision': 2000}, 200)
    assert sum(alloc.values()) == 200
    assert alloc['directive'] > 200 * 200 / 4200          # small group gets more than its plain share
    assert ms.sqrt_allocation({'a': 3, 'b': 1000}, 50) == {'a': 2, 'b': 48}
    assert sum(ms.sqrt_allocation({'a': 3, 'b': 4}, 50).values()) == 7


def test_sample_sheet_and_label_check():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        data, labels = tmp / 'data', tmp / 'labels'
        data.mkdir()
        fake_export(data)
        result = run('tools/make_label_sample.py', '--data', str(data), '--out', str(labels),
                     '--near', '40', '--far', '30', '--blocked', '10')
        assert result.returncode == 0, result.stderr
        sample = json.loads((labels / 'round1_sample.json').read_text(encoding='utf-8'))
        items = sample['items']
        assert len(items) == 80 and len({i['celex'] for i in items}) == 80
        assert {i['split'] for i in items} == {'dev', 'test'}
        assert abs(sum(i['split'] == 'test' for i in items) - 40) <= 9
        assert sum(s['picked'] for s in sample['strata'] if s['stratum'].startswith('blocked')) == 10
        # never overwrite an existing round
        assert run('tools/make_label_sample.py', '--data', str(data), '--out', str(labels)).returncode != 0

        wb = load_workbook(labels / 'round1_labelling.xlsx')
        sheet = wb['Label']
        header = [c.value for c in sheet[1]]
        col = {name: header.index(name) + 1 for name in header}
        assert len(sheet['F']) == 81 and sheet.cell(2, col['CELEX (link)']).hyperlink is not None
        fills = [('DIRECT', None, 'construction_products_ce', 'construction_product_manufacturer', None),
                 ('INDIRECT', 'purchased_products', 'machinery_noise_engine_emissions', 'fleet_operator', None),
                 ('IRRELEVANT', None, None, None, None),
                 ('INDIRECT', None, 'waste_management', 'waste_producer', None),          # missing path -> fix
                 ('MAYBE', None, None, None, None),                                       # bad level -> fix
                 ('DIRECT', None, 'public_procurement', 'drone_operator', 'odd role')]    # warning only
        for r, (level, path, area, role, note) in enumerate(fills, 2):
            for name, value in (('Level', level), ('Path (only INDIRECT)', path), ('Business area', area),
                                ('Role', role), ('Note (why)', note)):
                sheet.cell(r, col[name], value)
        sheet.move_range('A2:K3', rows=200)        # the user may sort: rows are matched by CELEX
        wb.save(labels / 'round1_labelling.xlsx')

        result = run('tools/check_labels.py', '--labels', str(labels))
        assert result.returncode == 0, result.stderr
        assert 'Labelled: 4 of 80' in result.stdout, result.stdout
        assert 'INDIRECT needs a path' in result.stdout and 'is not one of' in result.stdout
        assert 'not listed for public_procurement' in result.stdout
        saved = [json.loads(line) for line in (labels / 'round1_labels.jsonl').read_text(encoding='utf-8').splitlines()]
        assert sorted(x['level'] for x in saved) == ['DIRECT', 'DIRECT', 'INDIRECT', 'IRRELEVANT']
        assert all(x['split'] in ('dev', 'test') and x['weight'] > 0 for x in saved)

        # round 2 never repeats round-1 acts
        result = run('tools/make_label_sample.py', '--data', str(data), '--out', str(labels), '--round', '2',
                     '--near', '20', '--far', '20', '--blocked', '5')
        assert result.returncode == 0, result.stderr
        second = json.loads((labels / 'round2_sample.json').read_text(encoding='utf-8'))['items']
        assert not {i['celex'] for i in second} & {i['celex'] for i in items}


if __name__ == '__main__':
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for test in tests:
        test()
    print(f'{len(tests)} tests passed')
