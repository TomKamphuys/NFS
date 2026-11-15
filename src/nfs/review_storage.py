"""Review report and explicit, scoped analysis-data cleanup."""
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

import numpy as np

from .review_capture import REPORT_COLUMNS

REVIEW_REPORT_NAME = 'measurement_review.json'


def _read_review_document(folder):
    path = Path(folder) / REVIEW_REPORT_NAME
    if path.exists():
        document = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(document, dict) or document.get('report') != 'HALS Measurement Review':
            raise ValueError('Invalid measurement review report')
        return {row['Filename']: row for row in document['files']}, document.get('analysis')
    return {}, None


def _json_value(value):
    if isinstance(value, np.ndarray):
        if np.iscomplexobj(value):
            return {'real': value.real.tolist(), 'imag': value.imag.tolist()}
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f'Cannot save review value of type {type(value).__name__}')


def _save_review_report(folder, entries, analysis=None):
    folder = Path(folder)
    document = dict(report='HALS Measurement Review',
                    updated_utc=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                    measurement_directory=str(folder),
                    files=[entries[name] for name in sorted(entries)], analysis=analysis)
    contents = json.dumps(document, default=_json_value, allow_nan=False, indent=2) + '\n'
    temporary = folder / f'.measurement_review.{uuid.uuid4().hex}.tmp'
    try:
        temporary.write_text(contents, encoding='utf-8')
        temporary.replace(folder / REVIEW_REPORT_NAME)
    finally:
        temporary.unlink(missing_ok=True)
    return folder / REVIEW_REPORT_NAME


def write_review_report(folder, rows, actions=None, *, identity=None):
    """Update one readable/reloadable JSON report, retaining reviewed action reasons."""
    try:
        entries, analysis = _read_review_document(folder)
    except (ValueError, KeyError, TypeError):
        if identity is None:
            raise
        # A successful rescan can replace an unreadable old results file.
        entries, analysis = {}, None
    for row in rows:
        name = row['filename'].removesuffix('.blacklisted')
        old = entries.get(name)
        status = row['status']
        if status == 'excluded':
            entry = old or dict(Filename=name, Status='Excluded', Finding='Excluded', Reason='Previously excluded recording.')
            entry['Status'] = 'Excluded'
        elif status == 'pass':
            if old is None:
                continue
            entry = old
            entry['Finding'] = 'Okay'
            if entry['Status'] in ('Pending review', 'Excluded'):
                entry['Status'] = 'Cleared by rescan'
        else:
            action = old['Status'] if old and old['Status'] in ('Restored', 'Re-captured') else 'Pending review'
            entry = dict(Filename=name, Status=action,
                         Finding={'repeat': 'Suspect', 'review': 'Review'}.get(status, status.replace('_', ' ').title()),
                         Reason=row.get('reason') or row.get('coverage', ''))
        entries[name] = entry
    for name, action in (actions or {}).items():
        name = name.removesuffix('.blacklisted')
        if name not in entries:
            entries[name] = dict(Filename=name, Status=action, Finding='Okay', Reason='Manually selected for review action.')
        entries[name]['Status'] = action
    if identity is not None:
        analysis = dict(schema='hals_measurement_review_v1',
                        completed_utc=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                        identity=identity, rows=[{key: value for key, value in row.items()
                                                 if key not in ('noise_group', 'evidence')} for row in rows])
    elif analysis is not None and actions:
        # Persist the action immediately, even if the app closes before its rescan.
        for row in analysis.get('rows', []):
            original = row['filename'].removesuffix('.blacklisted')
            action = actions.get(row['filename'], actions.get(original))
            if action == 'Excluded':
                row.update(filename=original+'.blacklisted', status='excluded', marked=False)
            elif action == 'Restored':
                row.update(filename=original, status='incomplete', marked=False,
                           reason='Restored recording; re-scan to refresh.')
    return _save_review_report(folder, entries, analysis)


def load_review_results(folder):
    """Restore compact numerical results from the same human-readable JSON report."""
    entries, analysis = _read_review_document(folder)
    if analysis is None:
        return None
    if not isinstance(analysis, dict) or analysis.get('schema') != 'hals_measurement_review_v1':
        raise ValueError('Unsupported saved measurement review format')
    rows = analysis['rows']
    if not isinstance(rows, list):
        raise ValueError('Invalid saved measurement review results')
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid saved measurement row')
        name = row['filename']
        if not isinstance(name, str) or Path(name).name != name or name in ('', '.', '..'):
            raise ValueError('Invalid filename in saved measurement review')
        if row['status'] not in ('repeat', 'review', 'pass', 'technical', 'incomplete',
                                 'insufficient_neighbours', 'excluded'):
            raise ValueError('Invalid saved measurement finding')
        if 'response' in row:
            response = row['response']
            real, imag = np.asarray(response['real'], dtype=float), np.asarray(response['imag'], dtype=float)
            if real.shape != (37,) or imag.shape != (37,) or not np.isfinite(real).all() or not np.isfinite(imag).all():
                raise ValueError('Invalid saved LF response')
            row['response'] = real+1j*imag
        if 'xyz' in row:
            row['xyz'] = np.asarray(row['xyz'], dtype=float)
            if row['xyz'].shape != (3,) or not np.isfinite(row['xyz']).all():
                raise ValueError('Invalid saved measurement coordinates')
        if not isinstance(row.get('metrics', {}), dict) or not isinstance(row.get('issues', []), list):
            raise ValueError('Invalid saved measurement details')
        if not isinstance(row.get('bands', []), list):
            raise ValueError('Invalid saved frequency bands')
        row['marked'] = False
    identity = tuple(tuple(item) for item in analysis['identity'])
    return dict(rows=rows, identity=identity)


def export_report(path, rows):
    with Path(path).open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=REPORT_COLUMNS + ['marked'], extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def dataset_identity(folder):
    """Detect capture changes while a review is running or displayed."""
    folder = Path(folder)
    return tuple(sorted((str(path.relative_to(folder)), path.stat().st_size, path.stat().st_mtime_ns)
                        for path in folder.rglob('*') if path.is_file() and not path.is_symlink()
                        and path.suffix.lower() in ('.wav', '.blacklisted', '.npz', '.json')
                        and path != folder / REVIEW_REPORT_NAME))


def analysis_deletion_plan(folder):
    """Only known generated artifacts inside the selected measurement set."""
    folder = Path(folder).resolve(strict=True)
    plan = []
    for directory in ('debug', 'Distortion', 'failed'):
        root = folder / directory
        if not root.exists():
            continue
        if root.is_symlink() or root.is_junction() or not root.resolve().is_relative_to(folder):
            raise ValueError('Analysis directory must be inside the measurement set')
        for path in root.rglob('*'):
            if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(folder):
                raise ValueError('Analysis data contains an external link')
            if path.is_file() and path.suffix.lower() in ('.wav', '.npz', '.json'):
                stat = path.stat()
                plan.append((str(path.relative_to(folder)), stat.st_size, stat.st_mtime_ns, stat.st_ino))
    return tuple(sorted(plan))


def delete_analysis_data(folder, reviewed_plan):
    """Preflight the confirmed list before deleting; leave linear IRs and reports intact."""
    folder = Path(folder).resolve(strict=True)
    if analysis_deletion_plan(folder) != tuple(reviewed_plan):
        raise ValueError('Analysis files changed since confirmation. Review the space estimate again.')
    freed = 0
    for relative, size, modified, inode in reviewed_plan:
        path = folder / relative
        stat = path.stat()
        if path.is_symlink() or not path.resolve().is_relative_to(folder) or (
                stat.st_size, stat.st_mtime_ns, stat.st_ino) != (size, modified, inode):
            raise ValueError('Analysis file changed during cleanup')
        path.unlink()
        freed += size
    return freed
