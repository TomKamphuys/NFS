"""Read-only comparison against the standalone IR checker on a supplied dataset."""
import os
for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('dataset', type=Path)
    parser.add_argument('legacy_tool', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--reuse-legacy', action='store_true', help='Reuse the existing legacy CSV from this output directory')
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    sys.path.insert(0, str(args.legacy_tool))
    from nfs.measurement_review import scan_directory
    from nfs.review_storage import export_report
    import ir_capture_quality as legacy
    folder = args.dataset / 'measurement_set' if (args.dataset/'measurement_set').is_dir() else args.dataset
    args.output.mkdir(parents=True, exist_ok=True)
    def run(label, function):
        started = time.perf_counter()
        def progress(phase, done, total):
            if done % 100 == 0 or done == total:
                print(f'{label}: {phase} {done}/{total}', flush=True)
        rows = function(folder, progress)
        elapsed = time.perf_counter()-started
        export_report(args.output/f'{label}.csv', rows)
        # Include only acoustic findings; electrical outcomes never influence this comparison.
        findings = {row['filename']: [issue for issue in row.get('issues', [])
                                     if issue['category'].startswith('Acoustic/')]
                    for row in rows}
        return rows, findings, elapsed
    new, new_findings, new_time = run('native', scan_directory)
    if args.reuse_legacy:
        import csv
        old = list(csv.DictReader((args.output/'legacy.csv').open(encoding='utf-8')))
        old_findings = {row['filename']: [issue for issue in json.loads(row['evidence'])['issues']
                                         if issue['category'].startswith('Acoustic/')] for row in old}
        old_time = json.loads((args.output/'comparison.json').read_text())['legacy_seconds']
    else:
        old, old_findings, old_time = run('legacy', legacy.scan_directory)
    codes = lambda findings, excluded=(): {name: sorted(issue['code'] for issue in issues if issue['code'] not in excluded)
                                         for name, issues in findings.items()}
    new_codes, old_codes = codes(new_findings), codes(old_findings, ('full_ir_prefix',))
    names = sorted(set(new_codes)|set(old_codes))
    comparison = dict(dataset=str(folder), native_seconds=new_time, legacy_seconds=old_time,
        native_count=len(new), legacy_count=len(old),
        native_issue_counts=dict(Counter(issue['code'] for issues in new_findings.values() for issue in issues)),
        legacy_issue_counts=dict(Counter(issue['code'] for issues in old_findings.values() for issue in issues)),
        native_coverage=dict(Counter(row.get('coverage', '') for row in new)),
        changed=[dict(filename=name, native=new_codes.get(name, []), legacy=old_codes.get(name, []))
                 for name in names if new_codes.get(name, []) != old_codes.get(name, [])],
        native_flagged={name: issues for name, issues in new_codes.items() if issues},
        legacy_flagged={name: issues for name, issues in old_codes.items() if issues},
        removed_prefix_findings=[name for name, issues in old_findings.items() if any(i['code']=='full_ir_prefix' for i in issues)])
    (args.output/'comparison.json').write_text(json.dumps(comparison, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in comparison.items() if key not in ('native_flagged', 'legacy_flagged', 'native_coverage')}, indent=2))

if __name__ == '__main__':
    main()
