"""Capture history, comparison and recoverable publication of approved attempts.

All decisions live in measurement_review.json. Only one canonical IR per point
is visible to normal non-recursive downstream imports; attempts are immutable.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import uuid

import numpy as np

from .measurement_review import coordinates, _read_ir, inspect_point, scan_directory, file_identity
from .review_capture import (capture_stem, full_ir_path, _capture_evidence, finalize_rows)
from .review_storage import (read_review_document, save_review_document, decode_review_row)


def load_state(folder):
    state = read_review_document(folder).get('capture_review', {'points': {}})
    if not isinstance(state, dict) or not isinstance(state.get('points'), dict):
        raise ValueError('Invalid capture history')
    for name, point in state['points'].items():
        if not isinstance(name, str) or point_name(dict(filename=name)) != name or not isinstance(point, dict):
            raise ValueError('Invalid capture history point')
        if not isinstance(point.get('attempts'), list):
            raise ValueError('Invalid capture attempt list')
        numbers = set()
        for attempt in point['attempts']:
            if (not isinstance(attempt, dict) or not isinstance(attempt.get('number'), int)
                    or attempt['number'] < 1 or attempt['number'] in numbers):
                raise ValueError('Invalid capture attempt number')
            numbers.add(attempt['number'])
            if not isinstance(attempt.get('folder'), str):
                raise ValueError('Invalid capture attempt directory')
            safe_path(folder, attempt['folder'])
    return state


def save_state(folder, state):
    document = read_review_document(folder)
    document['capture_review'] = state
    save_review_document(folder, document)


def safe_path(folder, relative):
    root = Path(folder).resolve()
    path = root / relative
    if (any(p.is_symlink() or p.is_junction() for p in (path, *path.parents) if p != root and p.is_relative_to(root))
            or not path.resolve().is_relative_to(root)):
        raise ValueError('Capture path must stay inside the measurement directory')
    return path


def point_name(row):
    name = row['filename'].removesuffix('.blacklisted')
    if Path(name).name != name or name in ('', '.', '..'):
        raise ValueError('Expected a measurement filename')
    return name


def bundle_files(folder, name, excluded=False):
    """Map active artifact paths to their names inside a self-contained attempt."""
    root = Path(folder)
    result = {}
    ir = root / (name+'.blacklisted' if excluded else name)
    if ir.exists():
        result[name] = ir
    full = full_ir_path(root, {'filename': name})
    if full.exists():
        result[str(full.relative_to(root))] = full
    debug = root/'debug'
    stem = capture_stem(name)
    if debug.exists():
        for path in debug.glob(stem+'_*'):
            if path.is_file() and path.suffix.lower() in ('.wav', '.json'):
                result[str(path.relative_to(root))] = path
        sidecar = debug/(stem+'_capture.json')
        if sidecar.exists():
            reference = safe_path(debug, json.loads(sidecar.read_text(encoding='utf-8'))['reference_file'])
            if not reference.exists():
                raise ValueError('Capture reference is missing; restore it before archiving')
            result[str(reference.relative_to(root))] = reference
        else:
            for path in (debug/'references').glob('*/*.npz'):
                result[str(path.relative_to(root))] = path
    for path in result.values():
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Capture bundle contains an external link')
    return result


def _copy_bundle(files, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(destination.name+'.'+uuid.uuid4().hex+'.partial')
    staging.mkdir()
    try:
        for relative, source in files.items():
            target = safe_path(staging, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        staging.rename(destination)
    finally:
        if staging.exists():
            if staging.is_symlink() or staging.is_junction() or staging.resolve().parent != destination.parent.resolve():
                raise ValueError('Invalid temporary capture directory')
            shutil.rmtree(staging)


def ensure_point(folder, state, row):
    name = point_name(row)
    if name in state['points']:
        return state['points'][name]
    try:
        _, position = coordinates(name)
    except ValueError:
        position = None  # Invalid filenames can still be excluded.
    point = dict(position=list(position) if position is not None else None, active=1, chosen=1, viewed=1,
                 excluded=row['status'] == 'excluded', applied_excluded=row['status'] == 'excluded',
                 needs_decision=False, attempts=[])
    state['points'][name] = point
    files = bundle_files(folder, name, row['status'] == 'excluded')
    if name in files:
        if row.get('identity') and file_identity(files[name]) != tuple(row['identity']):
            raise ValueError(f'{name} changed since review. Re-scan first.')
        relative = str(Path('review_attempts')/hashlib.sha256(name.encode()).hexdigest()[:20]/f'capture_0001_{uuid.uuid4().hex[:8]}')
        destination = safe_path(folder, relative)
        _copy_bundle(files, destination)
        point['attempts'].append(dict(number=1, folder=relative, complete=True,
                                      created_utc=datetime.now(timezone.utc).isoformat(),
                                      analysis={k: v for k, v in row.items() if k not in ('noise_group', 'evidence')}))
    else:
        point.update(active=None, chosen=None, viewed=None, needs_decision=True)
    return point


def register_missing(folder, name, position, reason):
    if not isinstance(name, str) or (Path(folder)/name).exists():
        return
    state = load_state(folder)
    if name not in state['points']:
        state['points'][name] = dict(position=list(position), active=None, chosen=None, viewed=None,
                                    excluded=False, applied_excluded=False, needs_decision=True,
                                    attempts=[], missing_reason=reason)
        save_state(folder, state)


def reserve_attempts(folder, rows):
    state = load_state(folder)
    jobs = []
    for row in rows:
        name = point_name(row)
        point = ensure_point(folder, state, row)
        number = max([a['number'] for a in point['attempts']] or [0])+1
        relative = str(Path('review_attempts')/hashlib.sha256(name.encode()).hexdigest()[:20]/
                       f'capture_{number:04d}_{uuid.uuid4().hex[:8]}')
        attempt = dict(number=number, folder=relative, complete=False, error='Capture did not complete',
                       created_utc=datetime.now(timezone.utc).isoformat())
        point['attempts'].append(attempt)
        point['viewed'] = number
        point['needs_decision'] = True
        jobs.append(dict(filename=name, position=point['position'], number=number, folder=relative))
    save_state(folder, state)
    return jobs


def validate_recapture_settings(folder, rows, audio):
    """Keep comparisons meaningful; silence and retry settings may be adjusted."""
    for row in rows:
        sidecar = Path(folder)/'debug'/(capture_stem(point_name(row))+'_capture.json')
        if not sidecar.exists():
            continue  # Legacy/cleaned captures have no settings to validate.
        metadata = json.loads(sidecar.read_text(encoding='utf-8'))
        for key in ('fs', 'ch_in_mic', 'ch_in_loop', 'ch_out_spkr', 'ch_out_ref'):
            old = metadata.get('hardware_config', {})
            if key in old and old[key] != audio.hw.get(key):
                raise ValueError(f'Re-capture requires the original {key} setting ({old[key]}).')
        for key in ('sweep_dur_s', 'sweep_level_dbfs', 'num_sweeps', 'loopback_correction'):
            old = metadata.get('capture_config', {})
            if key in old and old[key] != audio.cap.get(key):
                raise ValueError(f'Re-capture requires the original {key} setting ({old[key]}).')
        if 'speaker_protection_hpf' in metadata:
            protection = getattr(audio, 'protection_filter', None)
            current = {key: getattr(protection, key) for key in
                       ('freq_hz', 'order', 'hpf_correction', 'hpf_corr_db_cap')} if protection else None
            if metadata['speaker_protection_hpf'] != current:
                raise ValueError('Restore the original speaker protection settings before re-capturing.')


def complete_attempt(folder, job, result=None, error=None):
    state = load_state(folder)
    point = state['points'][job['filename']]
    attempt = next(a for a in point['attempts'] if a['number'] == job['number'])
    path = safe_path(folder, job['folder'])/job['filename']
    succeeded = not error and not (result or {}).get('error') and path.is_file()
    attempt.update(complete=succeeded, error='' if succeeded else str(error or (result or {}).get('error') or 'No IR saved'))
    attempt['finished_utc'] = datetime.now(timezone.utc).isoformat()
    attempt['measured_position'] = (result or {}).get('measured_position')
    state.setdefault('actions', []).append(dict(timestamp=datetime.now(timezone.utc).isoformat(),
        filename=job['filename'], capture=job['number'], action='Re-captured' if succeeded else 'Re-capture failed',
        reason=attempt['error']))
    document = read_review_document(folder)
    entries = {row['Filename']: row for row in document.get('files', [])}
    entry = entries.setdefault(job['filename'], dict(Filename=job['filename'], Finding='Awaiting review', Reason=''))
    entry.update(Status='Re-captured' if succeeded else 'Re-capture failed',
                 Reason=entry.get('Reason') or attempt['error'] or 'New capture awaiting review')
    document.update(files=list(entries.values()), capture_review=state)
    save_review_document(folder, document)


def _candidate_noise(row, baseline):
    for key, code, message in (('pre', 'pre_sweep_noise', 'Elevated environmental microphone noise before the marker.'),
                                ('tail', 'late_noise', 'Unusual late microphone energy; noise or room decay.')):
        if f'mic_{key}_dbfs' not in row['metrics']:
            continue
        cohort = [r for r in baseline if r.get('noise_group') == row.get('noise_group')
                  and f'mic_{key}_dbfs' in r.get('metrics', {})]
        if len(cohort) < 8:
            row['coverage_notes'].append(f'{key} noise baseline unavailable (need 9 matching captures)')
            continue
        values = np.array([r['metrics'][f'mic_{key}_dbfs'] for r in cohort])
        order = np.argsort([np.linalg.norm(r['xyz']-row['xyz']) for r in cohort])[:12]
        def limit(data):
            median = np.median(data)
            return float(median+max(12., 6*max(1., 1.4826*np.median(abs(data-median)))))
        threshold = max(limit(values), limit(values[order]))
        row['metrics'][f'mic_{key}_threshold_dbfs'] = threshold
        if row['metrics'][f'mic_{key}_dbfs'] > threshold and row['metrics'][f'mic_{key}_relative_db'] > -45:
            row['issues'].append(dict(category='Acoustic/microphone', code=code, severity='review',
                                      message=message, threshold_db=threshold))


def _analyse_attempt(task):
    folder, name, attempt, baseline = task
    path = safe_path(folder, attempt['folder'])
    row = _read_ir(path/name)
    if 'response' in row:
        neighbours = sorted(baseline, key=lambda r: float(np.linalg.norm(r['xyz']-row['xyz'])))[:12]
        if len(neighbours) == 12:
            xyz = np.array([row['xyz']]+[r['xyz'] for r in neighbours])
            response = np.array([row['response']]+[r['response'] for r in neighbours])
            row.update(inspect_point(xyz, response, 0, np.arange(13)[None, :]))
            row['neighbours'] = [r['filename'] for r in neighbours]
        else:
            row.update(status='insufficient_neighbours', bands=[], reason='At least 12 other valid positions are required.')
    row = _capture_evidence((path, row))
    _candidate_noise(row, baseline)
    finalize_rows([row])
    row['review_folder'] = attempt['folder']
    return row


def scan_review_directory(folder, progress=lambda *args: None, cancelled=lambda: False, *, pool=None):
    recover_publication(folder)
    state = load_state(folder)
    try:
        rows = scan_directory(folder, progress, cancelled, pool=pool)
    except ValueError:
        if not state['points'] or any(Path(folder).glob('*.wav')):
            raise
        rows = []
    baseline = [r for r in rows if 'response' in r and 'xyz' in r and r['status'] != 'technical']
    tasks, targets = [], []
    for name, point in state['points'].items():
        try:
            target_xyz = coordinates(name)[0]
        except ValueError:
            target_xyz = None
        others = [r for r in baseline if point_name(r) != name and
                  (target_xyz is None or np.linalg.norm(r['xyz']-target_xyz) > 1e-9)]
        for attempt in point['attempts']:
            if attempt['complete']:
                tasks.append((folder, name, attempt, others))
                targets.append(attempt)
            else:
                attempt['analysis'] = dict(filename=name, status='technical', score=0., bands=[],
                    reason=attempt.get('error') or 'Capture unavailable', marked=False, metrics={}, issues=[],
                    review_folder=attempt['folder'], coverage='Capture failed')
    results = pool.map(_analyse_attempt, tasks, cancelled) if pool else map(_analyse_attempt, tasks)
    for index, result in enumerate(results):
        if cancelled():
            raise InterruptedError('Scan cancelled.')
        targets[index]['analysis'] = {k: v for k, v in result.items() if k not in ('noise_group', 'evidence')}
        progress('Comparing capture attempts', index+1, len(tasks))
    known = {point_name(row) for row in rows}
    for name, point in state['points'].items():
        if name not in known:
            rows.append(dict(filename=name, status='incomplete', score=0., bands=[], marked=False,
                             cylindrical=point['position'], xyz=coordinates(name)[0],
                             reason=point.get('missing_reason', 'Missing capture'),
                             issues=[dict(code='missing_capture')], metrics={}, coverage='No active IR'))
    save_state(folder, state)
    return rows


def attempt_row(name, attempt):
    row = decode_review_row(attempt.get('analysis', dict(filename=name, status='incomplete',
        score=0., bands=[], metrics={}, issues=[], reason='Re-scan to analyse this attempt')))
    row['filename'] = name
    row['review_folder'] = attempt['folder']
    return row


def attempt_quality(attempt):
    status = attempt.get('analysis', {}).get('status', 'incomplete')
    return 'pass' if status == 'pass' else 'fail' if status in ('repeat', 'review', 'technical') else 'incomplete'


def accept_capture_list(folder):
    """Publish every proposal as a recoverable batch, with immutable rollback sources."""
    recover_publication(folder)
    state = load_state(folder)
    operations, updates = [], {}
    for name, point in state['points'].items():
        if (Path(folder)/name).exists() and (Path(folder)/(name+'.blacklisted')).exists():
            raise ValueError(f'{name}: both an active and excluded file exist. Resolve the duplicate before approval.')
        chosen = point.get('chosen')
        if chosen is None and not point['excluded']:
            raise ValueError(f'Choose a capture or exclude {name} first.')
        attempt = next((a for a in point['attempts'] if a['number'] == chosen), None)
        if not point['excluded'] and (attempt is None or not attempt['complete']):
            raise ValueError(f'{name}: the chosen capture has no complete IR.')
        if attempt and not (safe_path(folder, attempt['folder'])/name).is_file():
            raise ValueError(f'{name}: archived capture is missing.')
        changes_capture = chosen != point.get('active') and not point['excluded']
        active = next((a for a in point['attempts'] if a['number'] == point.get('active')), None)
        if active:
            canonical = safe_path(folder, name+'.blacklisted' if point.get('applied_excluded') else name)
            archived = safe_path(folder, active['folder'])/name
            if not canonical.is_file() or not archived.is_file() or hashlib.sha256(canonical.read_bytes()).digest() != hashlib.sha256(archived.read_bytes()).digest():
                raise ValueError(f'{name}: active IR changed outside review. Restore the archived active capture before approval.')
        if changes_capture:
            source = safe_path(folder, attempt['folder'])
            new_files = bundle_files(source, name)
            old_files = bundle_files(folder, name, point.get('applied_excluded', False))
            active = next((a for a in point['attempts'] if a['number'] == point.get('active')), None)
            old_source = safe_path(folder, active['folder']) if active else None
            # Publish capture-specific artifacts; shared references are immutable/hash named.
            relative_paths = set(new_files)|set(old_files)
            for relative in sorted(relative_paths, key=lambda p: p == name):
                if Path(relative).parts[:2] == ('debug', 'references'):
                    if relative not in new_files or safe_path(folder, relative).exists():
                        continue
                old = old_source/relative if old_source and (old_source/relative).is_file() else None
                if relative in old_files and old is None:
                    raise ValueError(f'{name}: active analysis data changed outside review; restore it before approval.')
                if relative in old_files and hashlib.sha256(old_files[relative].read_bytes()).digest() != hashlib.sha256(old.read_bytes()).digest():
                    raise ValueError(f'{name}: active analysis data changed outside review; restore it before approval.')
                if relative == name and point.get('applied_excluded'):
                    old = None  # The canonical .wav did not exist before this transaction.
                operations.append(dict(target=relative, source=str((source/relative).relative_to(folder)) if relative in new_files else None,
                                       rollback=str(old.relative_to(folder)) if old else None))
            if point.get('applied_excluded'):
                operations.append(dict(target=name+'.blacklisted', source=None,
                                       rollback=str((old_source/name).relative_to(folder)) if old_source else None))
        elif point['excluded'] != point.get('applied_excluded', False):
            # Toggle the canonical IR only; every alternative remains archived.
            current = name+'.blacklisted' if point.get('applied_excluded') else name
            desired = name+'.blacklisted' if point['excluded'] else name
            active = next((a for a in point['attempts'] if a['number'] == point.get('active')), None)
            if active:
                archived = str((Path(active['folder'])/name))
                operations.extend([dict(target=desired, source=archived, rollback=None),
                                   dict(target=current, source=None, rollback=archived)])
        updated = deepcopy(point)
        updated.update(active=chosen if not point['excluded'] else point.get('active'),
                       applied_excluded=point['excluded'], needs_decision=False)
        updates[name] = updated
    # Record intent before touching canonical files. An interrupted activation is rolled back.
    state['publication'] = dict(operations=operations, updates=updates)
    save_state(folder, state)
    try:
        for op in operations:
            _publish_file(folder, op['target'], op['source'])
        state['points'].update(updates)
        state.pop('publication')
        state['accepted_utc'] = datetime.now(timezone.utc).isoformat()
        state.setdefault('actions', []).extend(dict(timestamp=state['accepted_utc'], filename=name,
            capture=point['active'], action='Excluded' if point['excluded'] else 'Approved capture')
            for name, point in updates.items())
        save_state(folder, state)
    except BaseException:
        recover_publication(folder)
        raise


def _publish_file(folder, relative, source):
    target = safe_path(folder, relative)
    if source is None:
        target.unlink(missing_ok=True)
        return
    origin = safe_path(folder, source)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        shutil.copy2(origin, temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def recover_publication(folder):
    state = load_state(folder)
    pending = state.get('publication')
    if pending:
        for op in reversed(pending['operations']):
            _publish_file(folder, op['target'], op['rollback'])
        state.pop('publication')
        save_state(folder, state)
