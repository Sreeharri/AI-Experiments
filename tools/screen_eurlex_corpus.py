"""Screen an exported database corpus with v9 evidence policy; resumable."""
from __future__ import annotations
import argparse
from collections import Counter
import csv
from datetime import date,datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from lr_ai.stored_corpus import StoredCorpus,validate_celex


def exclusions(path,known):
    if path is None:return []
    with path.open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if not {'celex','reason'}<=set(reader.fieldnames or []):raise ValueError('Exclusion CSV requires celex and reason columns')
        rows=list(reader)
    seen=set()
    for row in rows:
        row['celex']=validate_celex(row.get('celex'))
        if row['celex'] not in known:raise ValueError(f"Exclusion ID absent from corpus: {row['celex']}")
        if row['celex'] in seen:raise ValueError('Duplicate exclusion ID')
        if not str(row.get('reason') or '').strip():raise ValueError('Every approved exclusion requires a reason')
        seen.add(row['celex'])
    return rows


def summarize_predictions(out, selected, stored_acts, excluded_count, inference_exit_code):
    counts = Counter()
    missing = []
    unresolved = []
    failure_reasons = {}
    reviewed = 0
    for key in selected:
        path = out / 'predictions' / f'{key}.json'
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
        except FileNotFoundError:
            missing.append(key)
            continue
        except (OSError, ValueError):
            data = {'error': 'Saved result could not be read as JSON'}
        if not isinstance(data, dict) or data.get('celex', key) != key:
            data = {'error': 'Saved result does not match the selected act'}
        decision = data.get('decision')
        level = decision.get('level') if isinstance(decision, dict) else None
        status = data.get('status')
        if status == 'ok' and level in ('DIRECT', 'INDIRECT', 'IGNORE'):
            label = level
        elif status == 'prepared':
            label = 'PREPARED'
        else:
            label = 'UNRESOLVED'
            unresolved.append(key)
            reason = str(data.get('error') or 'Saved result has no valid screening decision')
            failure_reasons[key] = reason
        counts[label] += 1
        verification = data.get('verification')
        if isinstance(verification, dict) and verification.get('completed'):
            reviewed += 1
    return {
        'stored_acts': stored_acts, 'prefilter_excluded': excluded_count,
        'selected': len(selected), 'counts': dict(counts),
        'missing_ids': missing, 'unresolved_ids': unresolved,
        'failure_reasons': failure_reasons, 'reviews_completed': reviewed,
        'inference_exit_code': inference_exit_code,
        'note': 'No whole-corpus precision/recall without independent labels. Inspect unresolved data and the excluded-act audit.',
    }


def report_saved_results(corpus, out):
    state = json.loads((out / 'corpus_run.json').read_text(encoding='utf-8-sig'))
    signature = state['signature']
    if signature['corpus_sha256'] != corpus.sha256:
        raise ValueError('Run belongs to a different corpus snapshot')
    selected = [validate_celex(key) for key in signature['selected_ids']]
    if not selected or len(set(selected)) != len(selected) or set(selected) - set(corpus.ids()):
        raise ValueError('Run state contains an invalid act selection')
    report = summarize_predictions(out, selected, len(corpus.ids()), len(signature['exclusions']), None)
    report_path = out / 'corpus_recovery_report.json'
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"Selected: {report['selected']}; missing: {len(report['missing_ids'])}; "
          f"unresolved: {len(report['unresolved_ids'])}")
    print(f"Saved counts: {report['counts']}")
    reasons = Counter(report['failure_reasons'].values())
    for reason, count in reasons.most_common(5):
        example = next(key for key, value in report['failure_reasons'].items() if value == reason)
        print(f'{count} failure(s), example {example}: {reason[:500]}')
    print(f'Recovery report: {report_path}', flush=True)
    return 2 if report['missing_ids'] or report['unresolved_ids'] else 0


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--env',type=Path,default=ROOT/'.env')
    p.add_argument('--exclude-approved',type=Path,help='Optional reviewed celex,reason CSV; no automatic title/geography exclusions')
    p.add_argument('--workers',type=int,choices=range(1,9),default=4)
    p.add_argument('--input-budget',type=int,default=34000)
    p.add_argument('--context-limit',type=int,default=40960)
    p.add_argument('--max-chunks',type=int,default=0)
    run_mode = p.add_mutually_exclusive_group()
    run_mode.add_argument('--resume', action='store_true')
    run_mode.add_argument('--report-only', action='store_true',
                          help='Inspect existing results without contacting Qwen')
    p.add_argument('--prepare-only',action='store_true',help='No Qwen discovery or inference')
    p.add_argument('--labels',type=Path,help='Optional evaluation of the labelled subset after corpus inference')
    args=p.parse_args(argv)
    try:
        corpus=StoredCorpus(args.input)
        if args.report_only:
            if args.labels or args.prepare_only or args.exclude_approved:
                raise ValueError('--report-only uses the original run state and saved results')
            return report_saved_results(corpus, args.out)
        approved=exclusions(args.exclude_approved,set(corpus.ids()))
        excluded={r['celex'] for r in approved}
        selected=[x for x in corpus.ids() if x not in excluded]
        if not selected:raise ValueError('No acts remain selected')
        if args.labels:
            from evaluate_document_screening import read_labels
            truth=read_labels(args.labels)
            if set(truth)&excluded:raise ValueError('Labelled acts are prefiltered; evaluate those exclusions separately before claiming pipeline recall')
            if set(truth)-set(selected):raise ValueError('Some labelled IDs are absent from the selected corpus')
        signature={'corpus_sha256':corpus.sha256,'selected_ids':selected,'exclusions':approved,
                   'as_of':corpus.manifest['as_of'],'workers':args.workers,
                   'input_budget':args.input_budget,'context_limit':args.context_limit,
                   'max_chunks':args.max_chunks,'prepare_only':args.prepare_only}
        state_path=args.out/'corpus_run.json'
        if args.resume:
            if not state_path.exists():raise ValueError('Resume state is missing')
            old=json.loads(state_path.read_text())
            if old['signature']!=signature:raise ValueError('Snapshot/selection/settings differ; use a new output folder')
        else:
            args.out.mkdir(parents=True,exist_ok=False)
        state={'signature':signature,'started_at':datetime.now(timezone.utc).isoformat(),'status':'running'}
        state_path.write_text(json.dumps(state,indent=2),encoding='utf-8')
        ids_file=(args.out/'selected_ids.txt').resolve()
        ids_file.write_text('\n'.join(selected)+'\n',encoding='utf-8')
        (args.out/'prefilter_excluded.json').write_text(json.dumps(approved,indent=2),encoding='utf-8')
        command=[sys.executable,'-u',str(ROOT/'tools/document_relevance.py'),'--input',str(args.input.resolve()),
                 '--ids-file',str(ids_file),'--as-of',corpus.manifest['as_of'],'--env',str(args.env.resolve()),
                 '--workers',str(args.workers),'--input-budget',str(args.input_budget),
                 '--context-limit',str(args.context_limit),'--max-chunks',str(args.max_chunks),
                 '--out',str((args.out/'predictions').resolve())]
        if args.prepare_only:command.append('--prepare-only')
        print(f'{len(corpus.ids())} stored acts; {len(approved)} explicitly excluded; {len(selected)} selected.',flush=True)
        print('Preparation only; no AI calls.' if args.prepare_only else 'Qwen screening and one review of initial IGNOREs.',flush=True)
        code=subprocess.call(command,cwd=ROOT)
        summary = summarize_predictions(args.out, selected, len(corpus.ids()), len(approved), code)
        (args.out/'corpus_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
        if args.labels and not args.prepare_only:
            from compare_screening_passes import evaluate_both_passes
            evaluation_out=args.out/('labelled_evaluation_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
            evaluation_out.mkdir()
            eval_code=evaluate_both_passes(args.labels,'Labels',args.out/'predictions',evaluation_out)
            if eval_code:code=code or eval_code
        state['status'] = ('completed' if not code and not summary['unresolved_ids']
                           and not summary['missing_ids'] else 'incomplete')
        state_path.write_text(json.dumps(state,indent=2),encoding='utf-8')
        print(json.dumps(summary,indent=2),flush=True)
        return 0 if state['status']=='completed' else 2
    except (OSError,ValueError,KeyError) as exc:
        print(f'Corpus screening stopped: {exc}',file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
