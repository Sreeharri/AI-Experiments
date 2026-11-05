"""Streaming access to a read-only database snapshot exported one act per file."""
import hashlib
import json
from pathlib import Path
import re


def validate_celex(value):
    value=str(value).strip().upper()
    if not re.fullmatch(r'[0-9][A-Z0-9()._-]{4,79}',value):
        raise ValueError(f'Unsupported/unsafe stored CELEX identifier: {value!r}')
    return value


class StoredCorpus:
    def __init__(self,path):
        self.root=Path(path).resolve()
        raw=(self.root/'corpus_manifest.json').read_bytes()
        self.manifest=json.loads(raw)
        if self.manifest.get('format')!='eurlex_stored_corpus_v1' or not self.manifest.get('complete'):
            raise ValueError('Corpus export is incomplete or unsupported')
        self.records={}
        for record in self.manifest['acts']:
            key=validate_celex(record['celex'])
            if key in self.records:raise ValueError(f'Duplicate corpus ID {key}')
            self.records[key]=record
        self.sha256=hashlib.sha256(raw).hexdigest()

    def ids(self):return list(self.records)

    def get(self,identifier):
        key=validate_celex(identifier)
        record=self.records[key]
        path=(self.root/record['file']).resolve()
        if not path.is_relative_to(self.root):raise ValueError('Corpus path escapes its root')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=record['sha256']:
            raise ValueError(f'Exported source changed for {key}; re-export or restore the snapshot')
        act=json.loads(raw)
        if act.get('celex')!=key:raise ValueError('Stored act ID disagrees with manifest')
        return act
