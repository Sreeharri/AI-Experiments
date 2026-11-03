"""Read the EUR-Lex export (data/eurlex/) made by tools/export_eurlex.py.

    from lr_ai.eurlex_data import load_acts, TextStore
    acts = load_acts()                       # {celex: act record}
    texts = TextStore('EN')                  # English text, read on demand
    text = texts.get('32024R3110')           # {'recitals': [...], 'articles': [...], ...} or None

The text files are large, so TextStore keeps only an index (CELEX -> position in the
file) in memory and reads one line when asked. The index is saved next to the file
(text_en.jsonl.idx.json) and rebuilt automatically when the file changes.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / 'data' / 'eurlex'

# EuroVoc domains: some English notices give only the domain's URI number (e.g. 100147)
# and no label. This table turns both forms into the two-digit domain code.
EUROVOC_DOMAIN_URI = {
    '100142': '04', '100143': '08', '100144': '10', '100145': '12', '100146': '16', '100147': '20',
    '100148': '24', '100149': '28', '100150': '32', '100151': '36', '100152': '40', '100153': '44',
    '100154': '48', '100155': '52', '100156': '56', '100157': '60', '100158': '64', '100159': '66',
    '100160': '68', '100161': '72', '100162': '76'}


def label(item, language='en'):
    """Label of a {'id','en','de'} entry in the wanted language, else the other one."""
    other = 'de' if language == 'en' else 'en'
    return item.get(language) or item.get(other) or item.get('id') or ''


def domain_codes(act):
    """Two-digit EuroVoc domain codes of an act, e.g. {'52', '66'}."""
    codes = set()
    for item in act.get('eurovoc_domains') or []:
        text = item.get('en') or item.get('de') or ''
        match = re.match(r'^(\d{2})\b', text)
        if match:
            codes.add(match.group(1))
        elif item.get('id') in EUROVOC_DOMAIN_URI:
            codes.add(EUROVOC_DOMAIN_URI[item['id']])
        elif re.fullmatch(r'\d{2}', item.get('id') or ''):
            codes.add(item['id'])
    return codes


def read_acts(folder=DATA):
    with open(Path(folder) / 'acts.jsonl', encoding='utf-8') as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def load_acts(folder=DATA):
    return {act['celex']: act for act in read_acts(folder)}


class TextStore:
    def __init__(self, language='EN', folder=DATA):
        self.path = Path(folder) / f'text_{language.lower()}.jsonl'
        self.index_path = self.path.with_name(self.path.name + '.idx.json')
        self._index = None

    def _stamp(self):
        stat = self.path.stat()
        return f'{stat.st_size}:{int(stat.st_mtime)}'

    def index(self):
        if self._index is not None:
            return self._index
        stamp = self._stamp()
        if self.index_path.exists():
            saved = json.loads(self.index_path.read_text(encoding='utf-8'))
            if saved.get('stamp') == stamp:
                self._index = saved['offsets']
                return self._index
        offsets, position = {}, 0
        with open(self.path, 'rb') as handle:
            for line in handle:
                if line.strip():
                    start = line[:80].decode('utf-8', 'ignore')       # lines start with {"celex": "..."
                    celex = start.split('"celex": "', 1)[1].split('"', 1)[0] if '"celex": "' in start else \
                        json.loads(line)['celex']
                    offsets[celex] = position
                position += len(line)
        self.index_path.write_text(json.dumps({'stamp': stamp, 'offsets': offsets}), encoding='utf-8')
        self._index = offsets
        return offsets

    def __contains__(self, celex):
        return celex in self.index()

    def get(self, celex):
        offset = self.index().get(celex)
        if offset is None:
            return None
        with open(self.path, 'rb') as handle:
            handle.seek(offset)
            return json.loads(handle.readline())
