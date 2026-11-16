#!/usr/bin/env python3
"""Check that domains_de.yaml and domains_en.yaml match.

Checks:
- both files have the same version, the same domains in the same order and
  the same fields per domain
- numbers and departments are identical in both files
- no title, scope or boundary text is empty
- every domain named in a boundary (distinguish) exists
- every domain used by a business anchor exists (after the overrides)

Usage (in the AI experiments folder):
    py tools\\check_domains.py
Needs PyYAML. Exit code 0 = everything matches.
"""
import argparse
import re
import sys
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent.parent / 'config'
FIELDS = ['nr', 'title', 'label', 'scope', 'distinguish', 'departments']


def load(path):
    return yaml.safe_load(Path(path).read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--de', default=CONFIG / 'domains_de.yaml')
    parser.add_argument('--en', default=CONFIG / 'domains_en.yaml')
    parser.add_argument('--anchors', default=CONFIG / 'business_anchors_en.yaml')
    args = parser.parse_args()
    de, en = load(args.de), load(args.en)
    problems = []

    if de.get('version') != en.get('version'):
        problems.append(f"version differs: DE {de.get('version')} vs EN {en.get('version')}")
    if set(de) != set(en):
        problems.append(f'Top-level sections differ: {sorted(set(de) ^ set(en))}')
    a, b = de.get('domains') or {}, en.get('domains') or {}
    if list(a) != list(b):
        problems.append(f'domains: keys or order differ: only DE {sorted(set(a) - set(b))}, '
                        f'only EN {sorted(set(b) - set(a))}')
    for section in ('classification_rules', 'routing_hints'):
        if len(de.get(section) or []) != len(en.get(section) or []):
            problems.append(f'{section}: different number of entries')
    if list(de.get('fallback') or {}) != list(en.get('fallback') or {}):
        problems.append('fallback: keys differ')
    if de.get('anchor_domain_overrides') != en.get('anchor_domain_overrides'):
        problems.append('anchor_domain_overrides differ')

    for name, data in (('DE', de), ('EN', en)):
        domains = data.get('domains') or {}
        known = set(domains) | set(data.get('fallback') or {})
        numbers = [d.get('nr') for d in domains.values()]
        if numbers != list(range(1, len(domains) + 1)):
            problems.append(f'{name}: numbers are not 1..{len(domains)} in order')
        for key, domain in domains.items():
            if list(domain) != FIELDS:
                problems.append(f'{name} domains.{key}: fields are {list(domain)}, expected {FIELDS}')
            for field in ('title', 'label', 'scope', 'distinguish'):
                if not str(domain.get(field) or '').strip():
                    problems.append(f'{name} domains.{key}: empty {field}')
            for other in re.findall(r'\b[a-z]+(?:_[a-z]+)+\b', str(domain.get('distinguish') or '')):
                if other not in known:
                    problems.append(f'{name} domains.{key}: boundary names unknown domain {other}')
        for section in ('classification_rules', 'routing_hints'):
            for rule in data.get(section) or []:
                for other in re.findall(r'\b[a-z]+(?:_[a-z]+)+\b', rule):
                    if other not in known:
                        problems.append(f'{name} {section}: unknown domain {other}')
    for key in set(a) & set(b):
        for field in ('nr', 'departments'):
            if a[key].get(field) != b[key].get(field):
                problems.append(f'domains.{key}.{field}: DE {a[key].get(field)} vs EN {b[key].get(field)}')

    without_department = [key for key, domain in a.items() if not domain.get('departments')]
    anchors_path = Path(args.anchors)
    if anchors_path.exists():
        overrides = de.get('anchor_domain_overrides') or {}
        anchors = load(anchors_path).get('anchors') or {}
        for key in overrides:
            if key not in anchors:
                problems.append(f'anchor_domain_overrides: unknown anchor {key}')
        used = set()
        for key, anchor in anchors.items():
            domain = overrides[key] if key in overrides else anchor.get('domain')
            if domain is None:
                continue
            used.add(domain)
            if domain not in a:
                problems.append(f'anchor {key}: domain {domain} is not in the domain files')
        unused = sorted(set(a) - used)
        if unused:
            print(f'Note: domains without a business anchor: {unused}')
    else:
        print(f'(anchor file not found at {anchors_path}; anchor check skipped)')

    print(f"DE: {len(a)} domains · EN: {len(b)} domains · version {de.get('version')}")
    if without_department:
        print(f'Note: domains without a department yet: {without_department}')
    if problems:
        print('\nPROBLEMS:')
        for p in problems:
            print(' - ' + p)
        return 1
    print('OK: both files match.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
