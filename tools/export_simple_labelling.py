"""Export ID, Title, Evidence pack and blank Label from an existing run."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

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


def pack(record):
    return "\n\n".join(
        f"[{e.get('source_id', '')}] {e.get('type', '')}\n"
        + (str(e.get('heading') or '') + "\n" if e.get('heading') else "")
        + str(e.get('text') or '') for e in record.get('evidence', []))


def split_pack(text):
    return [text[i:i+30000] for i in range(0,len(text),30000)] or ['']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.worksheet.datavalidation import DataValidation
    try:
        if args.out.exists():raise ValueError('Output already exists; choose a new filename')
        if args.out.suffix.lower()!='.xlsx':raise ValueError('Output must be .xlsx')
        manifest,records=read_run(args.run)
        acts=read_acts(args.input,manifest['input_sha256'])
        wb=Workbook();ws=wb.active;ws.title='Labels'
        ws.append(['ID','Title','Evidence pack','Label'])
        ws.freeze_panes='C2';ws.sheet_view.showGridLines=False
        for col,width in [('A',20),('B',65),('C',115),('D',18)]:ws.column_dimensions[col].width=width
        ws.row_dimensions[1].height=30
        for c in ws[1]:
            c.fill=PatternFill('solid',fgColor='17365D');c.font=Font(color='FFFFFF',bold=True,size=11)
        validation=DataValidation(type='list',formula1='"DIRECT,INDIRECT,IGNORE"',allow_blank=True)
        validation.showErrorMessage=True;validation.errorStyle='stop';validation.error='Select DIRECT, INDIRECT or IGNORE.'
        ws.add_data_validation(validation)
        block_rows=[]
        for index,r in enumerate(records):
            first=ws.max_row+1
            a=acts[r['celex']];doc=a.get('document')or{}
            title=doc.get('title_en')or doc.get('title_de')or(a.get('instrument')or{}).get('title','')
            for n,text in enumerate(split_pack(pack(r))):
                ws.append([r['celex'] if n==0 else None,title if n==0 else None,text,None])
                row=ws.max_row;ws.row_dimensions[row].height=240
                for cell in ws[row]:
                    if isinstance(cell.value,str):
                        cell.value=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',lambda m:'\\u%04x'%ord(m.group()),cell.value)
                        cell.data_type='s'
                    cell.font=Font(name='Arial',size=11,color='243746')
                    cell.alignment=Alignment(wrap_text=True,vertical='top')
                    cell.fill=PatternFill('solid',fgColor='F1F5F9' if index%2==0 else 'FFFFFF')
                ws.cell(row,4).fill=PatternFill('solid',fgColor='FFF2CC')
            last=ws.max_row
            if last>first:
                for col in ['A','B','D']:ws.merge_cells(f'{col}{first}:{col}{last}')
            validation.add(ws.cell(first,4))
            block_rows.append((first,last))
        args.out.parent.mkdir(parents=True,exist_ok=True)
        temp=args.out.with_name(args.out.stem+'.part.xlsx')
        try:
            wb.save(temp)
            check=load_workbook(temp,read_only=True,data_only=False)
            if check['Labels'].max_column!=4:raise ValueError('Unexpected output columns')
            check.close();temp.replace(args.out)
        finally:temp.unlink(missing_ok=True)
        print(f'Exported {len(records)} acts to {args.out}; exactly four columns; one label cell per act.')
    except (OSError,ValueError,KeyError) as exc:parser.exit(1,f'Export failed: {exc}\n')


if __name__=='__main__':main()
