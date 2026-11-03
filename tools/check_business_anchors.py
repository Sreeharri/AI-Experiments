#!/usr/bin/env python3
"""Check that business_anchors_de.yaml and business_anchors_en.yaml match.

Checks:
- both files have the same top-level sections, levels, paths, roles,
  anchors and negative anchors, with the same fields per entry
- every role used by an anchor exists in "roles"
- every anchor domain exists in domains.yaml, and every domain has an anchor
- no text field is empty

Usage (in the AI experiments folder):
    py tools\\check_business_anchors.py
Needs PyYAML. Exit code 0 = everything matches.
"""
import argparse
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
CONFIG = HERE.parent / 'config'


def load(path):
    return yaml.safe_load(Path(path).read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--de', default=CONFIG / 'business_anchors_de.yaml')
    parser.add_argument('--en', default=CONFIG / 'business_anchors_en.yaml')
    parser.add_argument('--domains', default=CONFIG / 'domains.yaml')
    args = parser.parse_args()
    de, en = load(args.de), load(args.en)
    problems = []

    if set(de) != set(en):
        problems.append(f'Top-level sections differ: {sorted(set(de) ^ set(en))}')
    for section in ('levels', 'indirect_paths', 'roles', 'anchors', 'negative_anchors'):
        a, b = de.get(section) or {}, en.get(section) or {}
        if list(a) != list(b):
            problems.append(f'{section}: keys or order differ: only DE {sorted(set(a) - set(b))}, '
                            f'only EN {sorted(set(b) - set(a))}')
        for key in set(a) & set(b):
            if isinstance(a[key], dict) and set(a[key]) != set(b[key]):
                problems.append(f'{section}.{key}: fields differ ({sorted(set(a[key]) ^ set(b[key]))})')
            if isinstance(a[key], dict):
                for field in ('domain', 'roles'):
                    if a[key].get(field) != b[key].get(field):
                        problems.append(f'{section}.{key}.{field}: DE {a[key].get(field)} vs EN {b[key].get(field)}')

    for name, data in (('DE', de), ('EN', en)):
        roles = set(data.get('roles') or {})
        for key, anchor in (data.get('anchors') or {}).items():
            for role in anchor.get('roles') or []:
                if role not in roles:
                    problems.append(f'{name} anchors.{key}: unknown role {role}')
            for field in ('label', 'scope'):
                if not str(anchor.get(field) or '').strip():
                    problems.append(f'{name} anchors.{key}: empty {field}')
        for key, anchor in (data.get('negative_anchors') or {}).items():
            if not str(anchor.get('text') or '').strip():
                problems.append(f'{name} negative_anchors.{key}: empty text')
        unused = roles - {r for a in (data.get('anchors') or {}).values() for r in a.get('roles') or []}
        if unused:
            problems.append(f'{name}: roles never used by an anchor: {sorted(unused)}')

    domains_path = Path(args.domains)
    if domains_path.exists():
        domains = set((load(domains_path).get('domains') or {}))
        used = {a.get('domain') for a in (de.get('anchors') or {}).values()}
        for key, anchor in (de.get('anchors') or {}).items():
            if anchor.get('domain') not in domains:
                problems.append(f'anchors.{key}: domain {anchor.get("domain")} not in domains.yaml')
        missing = domains - used
        if missing:
            problems.append(f'domains without any anchor: {sorted(missing)}')
    else:
        print(f'(domains.yaml not found at {domains_path}; domain check skipped)')

    print(f"DE: {len(de.get('anchors') or {})} anchors, {len(de.get('negative_anchors') or {})} negative, "
          f"{len(de.get('roles') or {})} roles")
    print(f"EN: {len(en.get('anchors') or {})} anchors, {len(en.get('negative_anchors') or {})} negative, "
          f"{len(en.get('roles') or {})} roles")
    if problems:
        print('\nPROBLEMS:')
        for p in problems:
            print(' - ' + p)
        return 1
    print('OK: both files match, all roles exist, all domains covered.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
