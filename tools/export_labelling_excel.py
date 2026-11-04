"""Export a saved Qwen run to a human-labelling workbook. No model/DB calls."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LEVELS = ('DIRECT', 'INDIRECT', 'IGNORE')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_run(path):
    if path.is_dir():
        files = {p.name: p.read_bytes() for p in path.glob('*.json')}
    else:
        with zipfile.ZipFile(path) as archive:
            files = {}
            for name in archive.namelist():
                if name.endswith('.json'):
                    key = Path(name).name
                    if key in files:
                        raise ValueError('ZIP contains multiple run folders or duplicate filenames')
                    files[key] = archive.read(name)
    manifest = json.loads(files['manifest.json'].decode('utf-8-sig'))
    records = {}
    for name, raw in files.items():
        value = json.loads(raw.decode('utf-8-sig'))
        if isinstance(value, dict) and value.get('celex'):
            if value['celex'] in records:
                raise ValueError('Duplicate CELEX result')
            records[value['celex']] = value
    ids = manifest['selected_celex']
    if len(ids) != len(set(ids)) or set(ids) != set(records):
        raise ValueError('Saved results do not match the run manifest')
    return manifest, [records[key] for key in ids]


def read_acts(path, expected_hash):
    if path.suffix.lower() == '.zip':
        with zipfile.ZipFile(path) as archive:
            entries = [n for n in archive.namelist() if n.endswith('.json')]
            if len(entries) != 1:
                raise ValueError('Input ZIP must contain one complete-act JSON')
            raw = archive.read(entries[0])
    else:
        raw = path.read_bytes()
    if sha(raw) != expected_hash:
        raise ValueError('Act input fingerprint differs from the saved run')
    return {a['celex']: a for a in json.loads(raw.decode('utf-8-sig'))}


def parts(text, limit=1200):
    text = str(text or '')
    return [text[i:i + limit] for i in range(0, len(text), limit)] or ['']


def excel_text(value):
    # Excel's XML cannot store these control characters. Preserve them visibly.
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',
                  lambda m: '\\u%04x' % ord(m.group()), str(value or ''))


def profile_entries(section):
    """Preserve keyed sections and list-based sections such as not_roles."""
    if section is None:
        return []
    if isinstance(section, dict):
        return list(section.items())
    if isinstance(section, list):
        return [(str(value.get('id') or value.get('role_id') or f'entry_{index}')
                 if isinstance(value, dict) else str(value), value)
                for index, value in enumerate(section, 1)]
    raise ValueError('Business profile sections must be mappings or lists')


def evidence_rows(records):
    rows, first_rows = [], {}
    for record in records:
        first_rows[record['celex']] = len(rows) + 2
        for index, item in enumerate(record.get('evidence') or [], 1):
            fragments = parts(item.get('text'))
            for number, text in enumerate(fragments, 1):
                rows.append([record['celex'], index, item.get('source_id', ''),
                             item.get('type', ''), item.get('heading', ''),
                             number, len(fragments), text, item.get('path', ''),
                             item.get('source_version', ''), item.get('boundary_warning', '')])
    return rows, first_rows


def build(args):
    # Office-side dependency; spreadsheet generation is only performed on request.
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.table import Table, TableStyleInfo
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.formatting.rule import CellIsRule

    manifest, records = read_run(args.run)
    if len(records) != args.expected_count:
        raise ValueError(f'Expected {args.expected_count} results; found {len(records)}')
    acts = read_acts(args.input, manifest['input_sha256'])
    anchor_raw = args.anchors.read_bytes()
    if sha(anchor_raw) != manifest['anchor_sha256']:
        raise ValueError('Business anchor file differs from the profile used in this run')
    import yaml
    profile = yaml.safe_load(anchor_raw)
    prompt = args.prompt.read_text(encoding='utf-8-sig').strip()
    second = args.second_prompt.read_text(encoding='utf-8-sig').strip()
    prompt_matches = sha((prompt + second).encode()) == manifest['prompt_sha256']
    evidence, first_rows = evidence_rows(records)
    if args.out.exists():
        raise ValueError('Output already exists. Choose another --out; human labels must not be overwritten')
    wb = Workbook()
    wb.remove(wb.active)

    def sheet(name, headers, rows, widths, table_name):
        ws = wb.create_sheet(name)
        ws.append(headers)
        for row in rows:
            ws.append([excel_text(x) if isinstance(x, str) else x for x in row])
        ws.freeze_panes = 'C2'
        ws.sheet_view.showGridLines = False
        ws.row_dimensions[1].height = 32
        for cell in ws[1]:
            cell.fill = PatternFill('solid', fgColor='17365D')
            cell.font = Font(color='FFFFFF', bold=True, size=11)
            cell.alignment = Alignment(wrap_text=True, vertical='center')
        for col, width in widths.items(): ws.column_dimensions[col].width = width
        for row in ws.iter_rows(min_row=2):
            longest = 2
            for cell in row:
                cell.font = Font(size=11, color='243746')
                cell.alignment = Alignment(wrap_text=True, vertical='top')
                if isinstance(cell.value, str):
                    # Source strings beginning with '=' stay text, never formulas.
                    cell.data_type = 's'
                    width = ws.column_dimensions[cell.column_letter].width or 20
                    lines = sum(max(1, math.ceil(len(line) / max(10, width - 3)))
                                for line in cell.value.split('\n'))
                    longest = max(longest, lines)
            ws.row_dimensions[row[0].row].height = min(409, max(32, longest * 15 + 8))
        if rows:
            tab = Table(displayName=table_name, ref=ws.dimensions)
            tab.tableStyleInfo = TableStyleInfo(name='TableStyleMedium2', showRowStripes=True)
            ws.add_table(tab)
        return ws

    label_rows = []
    for r in records:
        act = acts[r['celex']]
        doc = act.get('document') or {}
        title = doc.get('title_en') or doc.get('title_de') or act.get('instrument', {}).get('title', '')
        label_rows.append([r['celex'], title, '', '', '', '', 'Open saved evidence',
                           f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{r['celex']}",
                           r.get('version_id', ''), manifest.get('as_of', '')])
    labels = sheet('Labels', ['celex', 'Act title', 'label', 'Human reason', 'Human anchor IDs',
                              'Review notes', 'Evidence', 'Official act link', 'Stored version ID', 'As of'],
                   label_rows, {'A':19,'B':65,'C':16,'D':55,'E':38,'F':45,'G':24,'H':32,'I':39,'J':14}, 'HumanLabels')
    for row in range(2, len(records) + 2):
        for col in range(3, 7):
            labels.cell(row, col).fill = PatternFill('solid', fgColor='FFF2CC')
            labels.cell(row, col).font = Font(color='0000FF', size=11)
        celex = labels.cell(row, 1).value
        if records[row - 2].get('evidence'):
            labels.cell(row, 7).hyperlink = f"#'Evidence'!A{first_rows[celex]}"
        else:
            labels.cell(row, 7, 'No saved evidence')
        labels.cell(row, 7).font = Font(color='0563C1', underline='single')
        labels.cell(row, 8).hyperlink = labels.cell(row, 8).value
    validation = DataValidation(type='list', formula1='"DIRECT,INDIRECT,IGNORE"', allow_blank=True)
    validation.errorTitle = 'Select a screening level'
    validation.error = 'Use DIRECT, INDIRECT or IGNORE.'
    validation.showErrorMessage = True
    validation.errorStyle = 'stop'
    labels.add_data_validation(validation)
    validation.add(f'C2:C{len(records)+1}')
    sheet('Evidence', ['celex','Passage','Source ID','Type','Stored heading (may be unreliable)',
                       'Text part','Parts','Exact saved passage text','Stored path','Source version','Boundary warning'],
          evidence, {'A':19,'B':10,'C':39,'D':18,'E':30,'F':10,'G':9,'H':115,'I':35,'J':42,'K':48}, 'SavedEvidence')
    guide = [
        ['Purpose','Label overall document relevance to Max Bögl. Detailed provision applicability is a later step.'],
        ['DIRECT','The act concerns the company’s own activities, products, operations, employees, rights or responsibilities in the supplied profile.'],
        ['INDIRECT','A credible connection exists through purchased products, suppliers, customers, project requirements, costs, markets or funding.'],
        ['IGNORE','No meaningful direct or indirect connection exists to any supplied business area.'],
        ['How to label','Read the title, business profile and saved evidence. Choose a level, explain the business connection in 1–2 sentences, and record matching anchor IDs. Leave uncertain cases blank and explain the gap in Review notes.'],
        ['Company facts','Do not invent manufacturing/importing or other business activities. Buying/using equipment is different from manufacturing it.'],
        ['Headings and directives','Read passage meaning when headings are unreliable. Addressing Member States does not automatically make a directive irrelevant.'],
        ['Independent labels','Label before opening Qwen_results. Qwen answers are predictions, not ground truth. Anchor matches need not use identical words.'],
        ['Evidence scope','Evidence contains every saved excerpt from the result file. It is not the whole act. Text parts concatenate in numeric order without added characters. The official link can show a different version.'],
        ['Rendering','If source text contains XML-forbidden control characters they appear as literal Unicode escape sequences. No passages are silently dropped. Expand rows or use the formula bar if Excel clips a display.'],
        ['Prompt fingerprint','MATCHES saved run' if prompt_matches else 'DOES NOT MATCH: supplied current prompt is not verified as the exact run prompt'],
    ]
    for i, fragment in enumerate(parts(prompt, 1200), 1):
        guide.append([f'Supplied decision prompt part {i}', fragment])
    if not prompt_matches:
        print('NOTICE: current prompt differs from saved-run fingerprint; workbook marks this clearly.')
    sheet('Decision_guide',['Topic','Guidance / supplied prompt'],guide,{'A':35,'B':115},'DecisionGuide')
    anchors = []
    for kind in ('anchors','roles','not_roles','indirect_paths','negative_anchors'):
        for key, value in profile_entries(profile.get(kind)):
            v = value if isinstance(value,dict) else {'scope':value}
            anchors.append([kind,key,v.get('label',''),str(v.get('scope','')),
                            json.dumps(v.get('roles',[]),ensure_ascii=False),str(v.get('note','')),
                            json.dumps(v.get('not_covered',''),ensure_ascii=False),json.dumps(value,ensure_ascii=False)])
    sheet('Business_profile',['Kind','ID','Label','Scope','Roles','Note','Not covered','Full entry JSON'],
          anchors,{'A':22,'B':42,'C':35,'D':85,'E':35,'F':85,'G':65,'H':90},'BusinessProfile')
    predictions = [[r['celex'],r.get('decision',{}).get('level',''),r.get('decision',{}).get('reason',''),
                    ', '.join(r.get('decision',{}).get('anchor_ids',[])),r.get('status',''),
                    json.dumps(r.get('data_warnings',[]),ensure_ascii=False),
                    json.dumps(r.get('data_issues',[]),ensure_ascii=False)] for r in records]
    sheet('Qwen_results',['celex','Predicted level','Qwen reason','Qwen anchor IDs','Run status','Warnings','Data issues'],
          predictions,{'A':19,'B':20,'C':100,'D':65,'E':18,'F':70,'G':70},'QwenPredictions')
    info = [['Result count',len(records)],['Saved evidence passages',sum(len(r.get('evidence',[])) for r in records)],
            ['Exported evidence text parts',len(evidence)],['Source run',args.run.name],
            ['Prompt fingerprint matches',str(prompt_matches)],['Created UTC',datetime.now(timezone.utc).isoformat()]]
    for key, value in manifest.items():
        for i, fragment in enumerate(parts(json.dumps(value,ensure_ascii=False),1200),1):
            info.append([f'Manifest {key} part {i}',fragment])
    for i, fragment in enumerate(parts(anchor_raw.decode('utf-8-sig'),1200),1):
        info.append([f'Exact supplied profile text part {i}',fragment])
    sheet('Run_info',['Field','Value / source text'],info,{'A':45,'B':115},'RunInfo')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temp = args.out.with_name(args.out.stem + '.part.xlsx')
    try:
        wb.save(temp)
        temp.replace(args.out)
    finally:
        temp.unlink(missing_ok=True)
    print(f'Exported {len(records)} acts and {len(evidence)} evidence text parts to {args.out}')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True,help='Saved run folder or ZIP')
    parser.add_argument('--input',type=Path,required=True,help='Original eligible_acts.json used by that run')
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--expected-count',type=int,default=200)
    parser.add_argument('--anchors',type=Path,default=ROOT/'config/business_anchors_en.yaml')
    parser.add_argument('--prompt',type=Path,default=ROOT/'prompts/document_relevance.txt')
    parser.add_argument('--second-prompt',type=Path,default=ROOT/'prompts/second_pass.txt')
    args=parser.parse_args()
    if args.out.suffix.lower()!='.xlsx':parser.error('--out must end in .xlsx')
    try:build(args)
    except (OSError,ValueError,KeyError,ImportError) as exc:parser.exit(1,f'Excel export failed: {exc}\n')
    return 0


if __name__=='__main__':raise SystemExit(main())
