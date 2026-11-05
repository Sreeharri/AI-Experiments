"""Extract selected IDs from a local structured export/catalogue; no LLM calls."""
from __future__ import annotations
import argparse
from datetime import date
import importlib.util
import json
from pathlib import Path
import sys
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
spec = importlib.util.spec_from_file_location('evidence_extraction_engine',ROOT/'lr_ai/document_filter.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


def extract_inputs(source, identifiers, output, as_of):
    source, output = Path(source),Path(output)
    identifiers = [str(x).strip().upper() for x in identifiers]
    if not identifiers or len(set(identifiers)) != len(identifiers):
        raise ValueError('Provide nonempty unique IDs')
    if any(not x or '/' in x or '\\' in x or '..' in x for x in identifiers):
        raise ValueError('Unsafe or empty ID')
    if source.is_dir():
        from lr_ai.catalogue_input import Catalogue
        catalogue = Catalogue(source)
        missing = set(identifiers)-set(catalogue.ids())
        if missing: raise ValueError(f'IDs absent from catalogue: {sorted(missing)}')
        acts = [catalogue.get(x) for x in identifiers]
    else:
        if source.suffix.lower()=='.zip':
            with ZipFile(source) as z:
                entries=[x for x in z.infolist() if not x.is_dir() and x.filename.lower().endswith('.json')]
                if len(entries)!=1 or entries[0].file_size>500_000_000:
                    raise ValueError('Input ZIP must contain one structured-act JSON under 500 MB')
                raw=z.read(entries[0])
        else: raw=source.read_bytes()
        data=json.loads(raw.decode('utf-8-sig'))
        if not isinstance(data,list):raise ValueError('Input must be a structured-act array, not prediction results')
        by_id={}
        for item in data:
            if not isinstance(item,dict) or not all(k in item for k in ('celex','version','document','instrument','elements')):
                raise ValueError('Input must contain complete structured act records')
            key=str(item['celex']).upper()
            if key in by_id:raise ValueError(f'Duplicate source ID {key}')
            by_id[key]=item
        missing=set(identifiers)-set(by_id)
        if missing:raise ValueError(f'IDs absent from source: {sorted(missing)}')
        acts=[by_id[x] for x in identifiers]
    output.mkdir(parents=True,exist_ok=False)
    packs=output/'prepared_evidence';packs.mkdir()
    audit=[]
    for act in acts:
        chunks,outline,issues=engine.prepare(act,as_of)
        item={'celex':act['celex'],'version':act['version'], 'document':act['document'],
              'chunks':chunks,'outline':outline,'issues':issues,
              'preparation':act.get('_evidence_preparation'),
              'note':'All prepared chunks, not necessarily all fit in a model request. Per-round evidence is saved with predictions.'}
        (packs/f"{act['celex']}.json").write_text(json.dumps(item,ensure_ascii=False,indent=2),encoding='utf-8')
        audit.append({'celex':act['celex'],'usable_chunks':len(chunks),'issues':issues,
                      'omitted_chunks':len((act.get('_evidence_preparation') or {}).get('omitted_chunks',[]))})
        act.pop('_evidence_preparation',None)
    target=output/'selected_acts.json'
    target.write_text(json.dumps(acts,ensure_ascii=False),encoding='utf-8')
    (output/'extraction.json').write_text(json.dumps({'source':str(source.resolve()),'selected_ids':identifiers,
             'assessment_date':as_of.isoformat(),'acts':audit,
             'source_access':'Existing stored export/catalogue; no database or website refresh performed'},indent=2),encoding='utf-8')
    return target


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--ids-file',type=Path,required=True,help='One CELEX ID per line')
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--as-of',type=date.fromisoformat,default=date(2026,11,4))
    args=p.parse_args()
    ids=[x.strip() for x in args.ids_file.read_text(encoding='utf-8-sig').splitlines() if x.strip()]
    try:print(extract_inputs(args.input,ids,args.out,args.as_of))
    except (OSError,ValueError,KeyError) as exc:p.exit(1,f'Extraction failed: {exc}\n')


if __name__=='__main__':main()
