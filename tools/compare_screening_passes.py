"""Evaluate saved first and final model decisions; never change their labels."""
from __future__ import annotations

import json
from pathlib import Path

from evaluate_document_screening import csv_write, main as evaluate_main, read_labels


def evaluate_both_passes(labels_path, sheet, run_dir, output):
    labels = read_labels(labels_path, sheet=sheet)
    run_dir, output = Path(run_dir), Path(output)
    first_dir = output / 'first_pass_predictions'
    first_dir.mkdir(exist_ok=False)
    manifest = run_dir / 'manifest.json'
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding='utf-8-sig'))
        data['evaluation_view'] = 'first_pass'
        (first_dir / 'manifest.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    flags = {}
    for identifier in labels:
        path = run_dir / f'{identifier}.json'
        if not path.exists():
            continue
        try:
            original = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(original, dict):
                raise ValueError('Prediction is not an object')
            first = original.get('first_pass_decision')
            flags[identifier] = original.get('verification') or {}
            view = dict(original)
            view['evaluation_view'] = 'first_pass'
            view['decision'] = first
            view['status'] = 'ok' if isinstance(first, dict) else 'failed'
            view['rounds'] = (original.get('rounds') or [])[:1]
            if not isinstance(first, dict):
                view['error'] = 'No completed first-pass decision saved'
        except (ValueError, TypeError, OSError) as exc:
            view = {'celex': identifier, 'status': 'failed', 'error': str(exc)}
        (first_dir / f'{identifier}.json').write_text(json.dumps(view, ensure_ascii=False, indent=2), encoding='utf-8')

    first_eval = output / 'evaluation_first_pass'
    final_eval = output / 'evaluation'
    first_code = evaluate_main(['--labels', str(labels_path), '--sheet', sheet,
                                '--run', str(first_dir), '--out', str(first_eval)])
    final_code = evaluate_main(['--labels', str(labels_path), '--sheet', sheet,
                                '--run', str(run_dir), '--out', str(final_eval)])
    if first_code == 1 or final_code == 1:
        return 1
    import csv
    def rows_at(path):
        with path.open(encoding='utf-8-sig', newline='') as stream:
            return {row['id']: row for row in csv.DictReader(stream)}
    first_rows = rows_at(first_eval / 'per_act.csv')
    final_rows = rows_at(final_eval / 'per_act.csv')
    first_metrics = json.loads((first_eval / 'metrics.json').read_text(encoding='utf-8'))
    final_metrics = json.loads((final_eval / 'metrics.json').read_text(encoding='utf-8'))
    transitions, recovered, added_fp, reviewed, failed_reviews = [], [], [], [], []
    for identifier in labels:
        a, b = first_rows[identifier], final_rows[identifier]
        flag = flags.get(identifier, {})
        if flag.get('required'):
            reviewed.append(identifier)
            if not flag.get('completed'):
                failed_reviews.append(identifier)
        if a['outcome'] == 'FN' and b['outcome'] == 'TP':
            recovered.append(identifier)
        if a['outcome'] == 'TN' and b['outcome'] == 'FP':
            added_fp.append(identifier)
        transitions.append({
            'id': identifier, 'title': labels[identifier]['title'], 'label': labels[identifier]['label'],
            'first_level': a['predicted_level'], 'final_level': b['predicted_level'],
            'first_outcome': a['outcome'], 'final_outcome': b['outcome'],
            'review_required': bool(flag.get('required')), 'review_completed': bool(flag.get('completed')),
            'first_reason': a['reason'], 'final_reason': b['reason'],
        })
    def metric_change(key):
        a, b = first_metrics['metrics'], final_metrics['metrics']
        if a is None or b is None or a[key] is None or b[key] is None:
            return None
        return b[key] - a[key]
    summary = {
        'complete': first_metrics['complete'] and final_metrics['complete'],
        'labelled_acts': len(labels), 'review_required_count': len(reviewed),
        'review_completed_count': len(reviewed) - len(failed_reviews),
        'review_failed_ids': failed_reviews,
        'recovered_relevant_count': len(recovered), 'recovered_relevant_ids': recovered,
        'additional_false_positive_count': len(added_fp), 'additional_false_positive_ids': added_fp,
        'first_confusion_matrix': first_metrics['confusion_matrix'],
        'final_confusion_matrix': final_metrics['confusion_matrix'],
        'first_metrics': first_metrics['metrics'], 'final_metrics': final_metrics['metrics'],
        'metric_changes': {k: metric_change(k) for k in ('precision', 'recall', 'accuracy', 'f1')},
        'note': 'Comparison of model decisions only. DIRECT and INDIRECT are both retained. No semantic overrides.',
    }
    (output / 'comparison.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2,
                                                    allow_nan=False) + '\n', encoding='utf-8')
    csv_write(output / 'review_changes.csv', transitions, list(transitions[0]))
    print(f"\nIGNORE review: {len(reviewed) - len(failed_reviews)}/{len(reviewed)} completed; "
          f"recovered relevant acts: {len(recovered)}; added false positives: {len(added_fp)}.")
    print(f"First-pass evaluation: {first_eval}")
    print(f"Final evaluation: {final_eval}")
    print(f"Comparison: {output / 'comparison.json'}")
    return 0 if summary['complete'] else 2
