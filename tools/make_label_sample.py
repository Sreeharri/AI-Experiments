#!/usr/bin/env python3
"""Pick EUR-Lex acts for hand labelling and build the Excel sheet to label them in.

Groups (each act lands in exactly one):
    near     kept acts with a EuroVoc topic area where Max Bögl's business mostly sits
             (social questions incl. construction, communications/IT, business, work,
             transport, environment, technology, energy, industry) - or no topic at all
    far      all other kept acts (trade, agriculture, finance, foreign relations, ...)
    blocked  acts the backend's title filter blocked (only titles are stored)
Inside each group the acts are split by type (regulation, directive, decision); small
types get a bit more than their share (square-root allocation). Every picked act gets a
weight (acts in its group and type / picked in its group and type) so results can later
be scaled back to all acts, and half of each group goes to "dev" (for improving the
prompt) and half to "test" (only for the final measurement).

The groups only decide how many acts we look at. They never decide relevance.

Usage (in the AI experiments folder, after tools\\export_eurlex.py):
    py tools\\make_label_sample.py                       400 acts: 200 near, 150 far, 50 blocked
    py tools\\make_label_sample.py --near 100 --far 75 --blocked 25 --round 2
Writes labels\\round1_sample.json and labels\\round1_labelling.xlsx (never overwrites them
unless --force is given). Needs: py -3 -m pip install openpyxl pyyaml
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lr_ai.eurlex_data import DATA, TextStore, domain_codes, label, read_acts  # noqa: E402

try:
    import yaml
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.worksheet.datavalidation import DataValidation
except ImportError:
    sys.exit('Missing packages. Run:  py -3 -m pip install openpyxl pyyaml')

NEAR_DOMAINS = {'28', '32', '40', '44', '48', '52', '64', '66', '68'}
LEVELS = ['DIRECT', 'INDIRECT', 'IRRELEVANT', 'UNSURE']
TYPE_NAMES = {'regulation': 'Regulation', 'directive': 'Directive', 'decision': 'Decision'}
EXCERPT_CHARS = 1200
FONT = 'Arial'


# ----------------------------------------------------------------------------- sampling
def group_of(act):
    if (act.get('filter') or {}).get('decision') == 'BLOCK':
        return 'blocked'
    codes = domain_codes(act)
    return 'near' if not codes or codes & NEAR_DOMAINS else 'far'


def sqrt_allocation(sizes, total):
    """Split `total` over strata in proportion to sqrt(size), never more than a stratum has."""
    total = min(total, sum(sizes.values()))
    alloc = {k: 0 for k in sizes}
    left = total
    while left > 0:
        open_ = {k: n for k, n in sizes.items() if alloc[k] < n}
        weight = sum(math.sqrt(n) for n in open_.values())
        shares = {k: left * math.sqrt(n) / weight for k, n in open_.items()}
        step = {k: min(int(s), sizes[k] - alloc[k]) for k, s in shares.items()}
        if sum(step.values()) == 0:                     # hand out the remainder one by one
            for k in sorted(open_, key=lambda k: shares[k] - int(shares[k]), reverse=True)[:left]:
                step[k] = 1
        for k, s in step.items():
            alloc[k] += s
        left = total - sum(alloc.values())
    return alloc


def draw(acts, wanted, seed, test_share):
    rng = random.Random(seed)
    population = collections.defaultdict(list)
    for act in sorted(acts, key=lambda a: a['celex']):
        title = act.get('title') or {}
        if title.get('en') or title.get('de'):
            population[(group_of(act), act['act_type'])].append(act)
    strata, picked = [], []
    for group, n_group in wanted.items():
        sizes = {t: len(population[(group, t)]) for t in TYPE_NAMES if population[(group, t)]}
        allocation = sqrt_allocation(sizes, n_group) if sizes else {}
        for act_type, n in allocation.items():
            members = population[(group, act_type)]
            chosen = rng.sample(members, n) if n else []
            weight = len(members) / n if n else None
            splits = ['test' if i < round(n * test_share) else 'dev' for i in range(n)]
            rng.shuffle(splits)
            for act, split in zip(chosen, splits):
                picked.append({'celex': act['celex'], 'group': group, 'act_type': act_type,
                               'stratum': f'{group}/{act_type}', 'weight': round(weight, 4), 'split': split})
            strata.append({'stratum': f'{group}/{act_type}', 'acts': len(members), 'picked': n,
                           'weight': round(weight, 4) if weight else None})
    rng.shuffle(picked)                                  # mixed order: the sheet shows no groups
    for number, item in enumerate(picked, 1):
        item['row'] = number
    return strata, picked


# ----------------------------------------------------------------------------- sheet content
def shorten(text, limit):
    text = ' '.join((text or '').split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def excerpt(act, texts):
    if (act.get('filter') or {}).get('decision') == 'BLOCK':
        return '(Blocked by the title filter: only the title is stored. Open the CELEX link if the title is not enough.)'
    for store in texts:
        text = store.get(act['celex']) if store.path.exists() else None
        if not text:
            continue
        articles = text.get('articles') or []
        first = [a for a in articles if (a.get('n') or '').strip() in ('1', '2')] or articles[:2]
        body = '\n\n'.join(a['text'] for a in first) or text.get('document_text') or ''
        if body:
            return shorten(body, EXCERPT_CHARS)
    return '(No text stored. Open the CELEX link.)'


def describe(act):
    kinds = [label(x) for x in act.get('resource_type_label') or []]
    kind = kinds[0] if kinds else TYPE_NAMES[act['act_type']]
    topics = '; '.join(label(x) for x in (act.get('eurovoc') or [])[:12])
    subject = '; '.join([label(x) for x in act.get('subject_matters') or []] +
                        [label(x).split(' > ')[-1] for x in act.get('directory_codes') or []])
    addressed = '; '.join(label(x) for x in act.get('addressees') or [])
    basis = []
    for item in act.get('legal_basis') or []:
        basis.append(item.get('label') or shorten(item.get('title_en') or item.get('title_de') or item['celex'], 90))
    changes = [f"{c['relation'].lower()} {c['celex']}: {shorten(c.get('title_en') or c.get('title_de') or '', 90)}"
               for c in (act.get('family') or {}).get('changes', [])[:3]]
    more = (act.get('family') or {}).get('changes_count', 0) - len(changes)
    if more > 0:
        changes.append(f'... and {more} more')
    title = act.get('title') or {}
    act_text = f"{kind} · {act['celex'][1:5]}\nEN: {title.get('en') or '-'}\nDE: {title.get('de') or '-'}"
    context = [('Topics', topics), ('Area', subject), ('Addressed to', addressed),
               ('Based on', '; '.join(basis)), ('Changes', '; '.join(changes))]
    return act_text, '\n'.join(f'{name}: {value}' for name, value in context if value)


# ----------------------------------------------------------------------------- workbook
COLUMNS = [  # header, width, filled by the labeller?
    ('No.', 5, False), ('CELEX (link)', 13, False), ('Act (type, year, titles)', 48, False),
    ('Topics and context', 42, False), ('Article 1 and 2 (start)', 72, False),
    ('Level', 13, True), ('Path (only INDIRECT)', 19, True), ('Business area', 27, True),
    ('Role', 24, True), ('Business area 2 (optional)', 24, True), ('Note (why)', 36, True)]
FIRST_INPUT = next(i for i, c in enumerate(COLUMNS, 1) if c[2])


def col_letter(col):
    letters = ''
    while col:
        col, rest = divmod(col - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


IN_FIRST, IN_LAST, INFO_LAST = col_letter(FIRST_INPUT), col_letter(len(COLUMNS)), col_letter(FIRST_INPUT - 1)


def build_workbook(path, picked, acts, anchors_en, anchors_de, texts, meta):
    wb = Workbook()
    thin = Side(style='thin', color='BFBFBF')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    base, bold = Font(name=FONT, size=10), Font(name=FONT, size=10, bold=True)
    head_fill, input_fill = PatternFill('solid', fgColor='D9E1F2'), PatternFill('solid', fgColor='FFF2CC')
    input_head = PatternFill('solid', fgColor='FFD966')
    wrap = Alignment(wrap_text=True, vertical='top')

    # --- Guide
    guide = wb.active
    guide.title = 'Guide'
    guide.column_dimensions['A'].width = 24
    guide.column_dimensions['B'].width = 110
    n = len(picked)
    rows = [
        ('Document-level relevance - labelling round ' + str(meta['round']), None),
        ('What to do', f'Open the sheet "Label". For every act fill the yellow columns {IN_FIRST} to {IN_LAST}. '
                       'Read title, topics and the start of Article 1 and 2; open the CELEX link when that is not enough.'),
        ('Level (required)', 'DIRECT, INDIRECT, IRRELEVANT or UNSURE (choose from the list).'),
        ('Path', 'Only for INDIRECT: how the act reaches Max Bögl (choose from the list).'),
        ('Business area', 'The business area that fits best (sheet "Lists"). Leave empty for IRRELEVANT.'),
        ('Role', 'The Max Bögl role that carries the duty (DIRECT) or is affected (INDIRECT). Leave empty for IRRELEVANT.'),
        ('Business area 2', 'Optional, when a second business area is clearly affected.'),
        ('Note', 'One short sentence why - helpful for UNSURE and for close calls.'),
        ('', ''),
        ('DIRECT', anchors_en['levels']['DIRECT']),
        ('INDIRECT', anchors_en['levels']['INDIRECT']),
        ('IRRELEVANT', anchors_en['levels']['IRRELEVANT']),
        ('UNSURE', 'You cannot decide even after opening the act. We look at these together; use it rarely.'),
        ('The rule', anchors_en['levels']['rule']),
        ('', ''),
    ]
    rows += [(f'Path: {k}', v) for k, v in anchors_en['indirect_paths'].items()]
    rows += [('', ''),
             ('Please', f'Label from the act itself, not from model answers. Do not change columns A to {INFO_LAST} or the order of rows.'),
             ('', ''),
             ('Progress', None), ('Labelled', f'=COUNTA(Label!{IN_FIRST}2:{IN_FIRST}{n + 1})'), ('Of', n),
             ('UNSURE so far', f'=COUNTIF(Label!{IN_FIRST}2:{IN_FIRST}{n + 1},"UNSURE")'),
             ('', ''), ('Example rows (format only)', None)]
    for r, (key, value) in enumerate(rows, 1):
        guide.cell(r, 1, key).font = bold
        cell = guide.cell(r, 2, value)
        cell.font = base
        cell.alignment = Alignment(wrap_text=True, vertical='top', horizontal='left')
        if isinstance(value, str) and len(value) > 120:      # Excel does not grow rows by itself
            guide.row_dimensions[r].height = 13.5 * math.ceil(len(value) / 120)
    guide['A1'].font = Font(name=FONT, size=14, bold=True)
    example_start = len(rows) + 1
    examples = [
        ('32024R3110 Construction products', 'DIRECT | Path: - | Business area: construction_products_ce | '
         'Role: construction_product_manufacturer | Note: Max Bögl makes precast elements and must issue declarations of performance.'),
        ('Engine emission limits for mobile machinery (example)', 'INDIRECT | Path: purchased_products | '
         'Business area: machinery_noise_engine_emissions | Role: fleet_operator | Note: duties are on engine makers; changes the machines we buy.'),
        ('Anti-dumping duty on bicycles (example)', 'IRRELEVANT | everything else empty'),
    ]
    for offset, (key, value) in enumerate(examples):
        guide.cell(example_start + offset, 1, key).font = base
        guide.cell(example_start + offset, 2, value).font = base
        guide.cell(example_start + offset, 1).alignment = wrap
    guide.cell(example_start + len(examples) + 1, 1,
               f"Sample: {meta['created']}, seed {meta['seed']}, export {meta['export']}").font = Font(name=FONT, size=8, color='808080')

    # --- Label
    sheet = wb.create_sheet('Label')
    for col, (header, width, is_input) in enumerate(COLUMNS, 1):
        cell = sheet.cell(1, col, header)
        cell.font, cell.fill, cell.border = bold, (input_head if is_input else head_fill), border
        cell.alignment = Alignment(wrap_text=True, vertical='center')
        sheet.column_dimensions[cell.column_letter].width = width
    for item in picked:
        act = acts[item['celex']]
        r = item['row'] + 1
        act_text, context = describe(act)
        values = [item['row'], act['celex'], act_text, context, excerpt(act, texts)]
        for col, value in enumerate(values, 1):
            cell = sheet.cell(r, col, value)
            cell.font, cell.alignment, cell.border = base, wrap, border
        link = sheet.cell(r, 2)
        link.hyperlink = f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{act['celex']}"
        link.font = Font(name=FONT, size=10, color='0563C1', underline='single')
        for col in range(FIRST_INPUT, len(COLUMNS) + 1):
            cell = sheet.cell(r, col)
            cell.fill, cell.font, cell.alignment, cell.border = input_fill, base, wrap, border
        sheet.row_dimensions[r].height = 165
    sheet.freeze_panes = 'C2'
    sheet.auto_filter.ref = f'A1:{IN_LAST}{n + 1}'

    # --- Lists (drop-down sources and explanations)
    lists = wb.create_sheet('Lists')
    heads = ['Business area', 'English', 'Deutsch', 'Domain', '', 'Role', 'English', 'Deutsch', '', 'Path', 'Meaning']
    for col, header in enumerate(heads, 1):
        if header:
            cell = lists.cell(1, col, header)
            cell.font, cell.fill = bold, head_fill
    for r, (key, anchor) in enumerate(anchors_en['anchors'].items(), 2):
        for col, value in enumerate([key, anchor['label'], anchors_de['anchors'][key]['label'], anchor['domain']], 1):
            lists.cell(r, col, value).font = base
    for r, (key, text) in enumerate(anchors_en['roles'].items(), 2):
        for col, value in enumerate([key, text, anchors_de['roles'][key]], 6):
            lists.cell(r, col, value).font = base
    for r, (key, text) in enumerate(anchors_en['indirect_paths'].items(), 2):
        lists.cell(r, 10, key).font = base
        lists.cell(r, 11, text).font = base
    for letter, width in zip('ABCDEFGHIJK', (32, 45, 45, 24, 3, 32, 60, 60, 3, 22, 70)):
        lists.column_dimensions[letter].width = width
    lists.freeze_panes = 'A2'

    # --- drop-downs
    n_anchor, n_role, n_path = len(anchors_en['anchors']), len(anchors_en['roles']), len(anchors_en['indirect_paths'])
    rules = [(FIRST_INPUT, '"' + ','.join(LEVELS) + '"'),
             (FIRST_INPUT + 1, f'=Lists!$J$2:$J${n_path + 1}'),
             (FIRST_INPUT + 2, f'=Lists!$A$2:$A${n_anchor + 1}'),
             (FIRST_INPUT + 3, f'=Lists!$F$2:$F${n_role + 1}'),
             (FIRST_INPUT + 4, f'=Lists!$A$2:$A${n_anchor + 1}')]
    for col, formula in rules:
        rule = DataValidation(type='list', formula1=formula, allow_blank=True, showErrorMessage=True,
                              errorTitle='Not in the list', error='Please choose a value from the list.')
        sheet.add_data_validation(rule)
        rule.add(f'{col_letter(col)}2:{col_letter(col)}{n + 1}')
    wb.save(path)


# ----------------------------------------------------------------------------- main
def main():
    parser = argparse.ArgumentParser(description='Pick acts for labelling and build the Excel sheet.')
    parser.add_argument('--near', type=int, default=200)
    parser.add_argument('--far', type=int, default=150)
    parser.add_argument('--blocked', type=int, default=50)
    parser.add_argument('--round', type=int, default=1)
    parser.add_argument('--seed', type=int, default=20261028)
    parser.add_argument('--test-share', type=float, default=0.5, help='share of each group kept for the final test')
    parser.add_argument('--data', default=str(DATA))
    parser.add_argument('--out', default=str(ROOT / 'labels'), help='folder for the sample and the Excel sheet')
    parser.add_argument('--force', action='store_true', help='overwrite existing round files')
    args = parser.parse_args()

    labels = Path(args.out)
    labels.mkdir(parents=True, exist_ok=True)
    sample_path = labels / f'round{args.round}_sample.json'
    sheet_path = labels / f'round{args.round}_labelling.xlsx'
    if not args.force and (sample_path.exists() or sheet_path.exists()):
        sys.exit(f'{sheet_path.name} already exists - it may hold your labels. Use another --round, or --force to replace it.')

    data = Path(args.data)
    if not (data / 'acts.jsonl').exists():
        sys.exit(f'{data / "acts.jsonl"} not found. Run tools\\export_eurlex.py first.')
    acts = {a['celex']: a for a in read_acts(data)}
    earlier = set()
    for old in sorted(labels.glob('round*_sample.json')):
        if old != sample_path:
            earlier |= {i['celex'] for i in json.loads(old.read_text(encoding='utf-8'))['items']}
    pool = [a for c, a in acts.items() if c not in earlier]       # never label an act twice
    wanted = {'near': args.near, 'far': args.far, 'blocked': args.blocked}
    strata, picked = draw(pool, wanted, args.seed + args.round, args.test_share)

    info_path = data / 'export_info.json'
    export_stamp = json.loads(info_path.read_text(encoding='utf-8')).get('finished', '?') if info_path.exists() else '?'
    meta = {'round': args.round, 'created': dt.datetime.now().isoformat(timespec='seconds'), 'seed': args.seed + args.round,
            'export': export_stamp, 'near_domains': sorted(NEAR_DOMAINS), 'requested': wanted,
            'test_share': args.test_share, 'excluded_from_earlier_rounds': len(earlier)}
    anchors_en = yaml.safe_load((ROOT / 'config' / 'business_anchors_en.yaml').read_text(encoding='utf-8'))
    anchors_de = yaml.safe_load((ROOT / 'config' / 'business_anchors_de.yaml').read_text(encoding='utf-8'))
    texts = [TextStore('EN', data), TextStore('DE', data)]
    build_workbook(sheet_path, picked, acts, anchors_en, anchors_de, texts, meta)
    sample_path.write_text(json.dumps({'meta': meta, 'strata': strata, 'items': picked}, ensure_ascii=False, indent=1),
                           encoding='utf-8')

    print(f'{"Group/type":22} {"acts":>7} {"picked":>7} {"weight":>8}')
    for s in strata:
        print(f'{s["stratum"]:22} {s["acts"]:>7,} {s["picked"]:>7} {s["weight"] or 0:>8.1f}')
    splits = collections.Counter(i['split'] for i in picked)
    print(f'\n{len(picked)} acts picked (dev {splits["dev"]}, test {splits["test"]}).')
    print(f'Label here:  {sheet_path}\nSample file: {sample_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
