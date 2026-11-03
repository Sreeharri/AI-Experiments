#!/usr/bin/env python3
"""Check the labelling sheet and save the labels as JSON lines.

Run it any time while labelling - it only reads the Excel file:
    py tools\\check_labels.py              round 1
    py tools\\check_labels.py --round 2

It shows how far you are, lists rows that need a fix, and writes
labels\\round1_labels.jsonl (one line per labelled act) for the experiments.
Rows are matched by CELEX, so sorting or filtering the sheet is fine.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:
    import yaml
    from openpyxl import load_workbook
except ImportError:
    sys.exit('Missing packages. Run:  py -3 -m pip install openpyxl pyyaml')

LEVELS = ('DIRECT', 'INDIRECT', 'IRRELEVANT', 'UNSURE')
FIELDS = {'level': 'Level', 'path': 'Path (only INDIRECT)', 'area': 'Business area', 'role': 'Role',
          'area2': 'Business area 2 (optional)', 'note': 'Note (why)', 'celex': 'CELEX (link)'}


def clean(value):
    return ' '.join(str(value).split()) if value is not None and str(value).strip() else None


def check_row(row, anchors):
    """Returns (errors, warnings) for one filled row."""
    errors, warnings = [], []
    level = (row['level'] or '').upper()
    if level not in LEVELS:
        errors.append(f'level "{row["level"]}" is not one of {", ".join(LEVELS)}')
        return errors, warnings
    for key in ('area', 'area2'):
        if row[key] and row[key] not in anchors['anchors']:
            errors.append(f'business area "{row[key]}" is not in the list')
    if row['role'] and row['role'] not in anchors['roles']:
        errors.append(f'role "{row["role"]}" is not in the list')
    if row['path'] and row['path'] not in anchors['indirect_paths']:
        errors.append(f'path "{row["path"]}" is not in the list')
    if level == 'INDIRECT' and not row['path']:
        errors.append('INDIRECT needs a path')
    if level != 'INDIRECT' and row['path']:
        warnings.append(f'path is only used for INDIRECT (level is {level})')
    if level in ('DIRECT', 'INDIRECT'):
        if not row['area']:
            errors.append(f'{level} needs a business area')
        if not row['role']:
            errors.append(f'{level} needs a role')
        if (level == 'DIRECT' and row['area'] in anchors['anchors'] and row['role'] in anchors['roles']
                and row['role'] not in anchors['anchors'][row['area']].get('roles', [])):
            warnings.append(f'role {row["role"]} is not listed for {row["area"]} in the anchors - '
                            'fine if correct; note it so we can extend the anchors')
    if level == 'IRRELEVANT' and (row['area'] or row['role']):
        warnings.append('IRRELEVANT usually has no business area or role')
    if level == 'UNSURE' and not row['note']:
        warnings.append('UNSURE: please add a note what makes it unclear')
    return errors, warnings


def main():
    parser = argparse.ArgumentParser(description='Check the labelling sheet and save the labels.')
    parser.add_argument('--round', type=int, default=1)
    parser.add_argument('--labels', default=str(ROOT / 'labels'))
    args = parser.parse_args()

    folder = Path(args.labels)
    sheet_path = folder / f'round{args.round}_labelling.xlsx'
    sample_path = folder / f'round{args.round}_sample.json'
    for path in (sheet_path, sample_path):
        if not path.exists():
            sys.exit(f'{path} not found. Run tools\\make_label_sample.py first.')
    sample = {i['celex']: i for i in json.loads(sample_path.read_text(encoding='utf-8'))['items']}
    anchors = yaml.safe_load((ROOT / 'config' / 'business_anchors_en.yaml').read_text(encoding='utf-8'))

    try:
        wb = load_workbook(sheet_path, read_only=True, data_only=True)
    except PermissionError:
        sys.exit(f'Cannot read {sheet_path.name}. Save it in Excel first (closing is not needed on most PCs).')
    rows = wb['Label'].iter_rows(values_only=True)
    header = [clean(h) for h in next(rows)]
    missing = [name for name in FIELDS.values() if name not in header]
    if missing:
        sys.exit(f'Columns missing in sheet "Label": {missing}. Please do not rename or delete columns.')
    index = {key: header.index(name) for key, name in FIELDS.items()}

    labelled, problems, seen = [], [], set()
    for number, values in enumerate(rows, 2):
        row = {key: clean(values[i]) if i < len(values) else None for key, i in index.items()}
        if not row['celex']:
            continue
        if row['celex'] not in sample:
            problems.append((number, row['celex'], ['this CELEX is not in the sample - was the column changed?'], []))
            continue
        seen.add(row['celex'])
        if not row['level']:
            if any(row[k] for k in ('path', 'area', 'role', 'area2', 'note')):
                problems.append((number, row['celex'], ['level is empty but other columns are filled'], []))
            continue
        errors, warnings = check_row(row, anchors)
        if errors or warnings:
            problems.append((number, row['celex'], errors, warnings))
        if errors:
            continue
        item = sample[row['celex']]
        labelled.append({
            'celex': row['celex'], 'level': row['level'].upper(), 'path': row['path'],
            'areas': [a for a in (row['area'], row['area2']) if a], 'role': row['role'], 'note': row['note'],
            'round': args.round, 'split': item['split'], 'group': item['group'], 'act_type': item['act_type'],
            'stratum': item['stratum'], 'weight': item['weight']})
    wb.close()

    out = folder / f'round{args.round}_labels.jsonl'
    with out.open('w', encoding='utf-8', newline='\n') as handle:
        for item in sorted(labelled, key=lambda x: x['celex']):
            handle.write(json.dumps(item, ensure_ascii=False) + '\n')

    total = len(sample)
    print(f'Labelled: {len(labelled)} of {total} ({100 * len(labelled) / max(total, 1):.0f}%)')
    levels = collections.Counter(x['level'] for x in labelled)
    print('Levels:   ' + (', '.join(f'{k} {levels[k]}' for k in LEVELS if levels[k]) or '-'))
    by_group = collections.defaultdict(collections.Counter)
    for x in labelled:
        by_group[x['group']][x['level']] += 1
    for group, counts in sorted(by_group.items()):
        print(f'  {group:8} ' + ', '.join(f'{k} {counts[k]}' for k in LEVELS if counts[k]))
    if len(seen) < total:
        print(f'\nNote: {total - len(seen)} sampled acts were not found in the sheet (rows deleted?).')
    errors = [p for p in problems if p[2]]
    if problems:
        print(f'\nTo fix ({len(errors)} rows not saved until fixed) / to look at:')
        for number, celex, errs, warns in sorted(problems):
            for text in errs:
                print(f'  row {number} {celex}: FIX  {text}')
            for text in warns:
                print(f'  row {number} {celex}: note {text}')
    else:
        print('\nNo problems found.')
    print(f'\nSaved: {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
