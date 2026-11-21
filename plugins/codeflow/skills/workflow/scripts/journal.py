#!/usr/bin/env python3
"""Local run evidence, diagnostics and optional Codex hook integration."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid

from flow import PROFILES, ROLES, Store, atomic, atomic_bytes, catalog_snapshot, config, now, overrides, read, resolve, user_config_path

ID = re.compile(r'[A-Za-z0-9_-]{1,100}')


def inside(store, relative):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError('Watch paths must be project-relative files: ' + relative)
    result = store.root / path
    if not result.resolve().is_relative_to(store.root):
        raise ValueError('Watch path leaves the project: ' + relative)
    return result


def fingerprint(store, paths):
    result = {}
    for name in paths:
        path = inside(store, name)
        if not path.exists():
            result[name] = None
            continue
        if not path.is_file():
            raise ValueError('Watch individual files rather than directories: ' + name)
        with path.open('rb') as stream:
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1 << 20), b''):
                digest.update(chunk)
        result[name] = digest.hexdigest()
    return result


def load_journal(directory):
    path = directory / 'journal.json'
    if path.is_symlink():
        raise ValueError('Journal cannot be a symlink')
    return read(path) if path.exists() else {'checks': [], 'agents': [], 'series': {}, 'usage_turns': []}


def edit(store, run_id, change):
    directory = store.directory(run_id)
    with store.lock(directory):
        store.load(run_id)
        data = load_journal(directory)
        result = change(data)
        atomic(directory / 'journal.json', data)
    return result


def changed_paths(store, snapshot):
    return [p for p, digest in fingerprint(store, snapshot).items() if digest != snapshot[p]]


def series_status(store, series, checks=()):
    latest = {check['item']: check for check in checks if check.get('item')}
    stale = set()
    for item_id, item in series.items():
        check = latest.get(item_id)
        failed = check is not None and (check['exit_code'] != 0 or check['changed_during_check'])
        if item['status'] == 'complete' and (failed or changed_paths(store, item.get('inputs', {}))):
            stale.add(item_id)
    while True:
        dependents = {item_id for item_id, item in series.items()
                      if item['status'] == 'complete' and any(
                          dep in stale or series[dep]['status'] != 'complete'
                          or series[dep].get('check_id') != item.get('dependency_checks', {}).get(dep)
                          for dep in item['depends_on'])}
        if dependents <= stale:
            break
        stale.update(dependents)
    return {key: {**item, 'effective_status': 'stale' if key in stale else item['status']}
            for key, item in series.items()}


def start_series(data, definition):
    if data['series']:
        raise ValueError('Series already exists')
    if not isinstance(definition, list) or not definition:
        raise ValueError('Series must be a nonempty JSON list')
    # Ordered dependencies make cycles and forward references impossible.
    for item in definition:
        if not isinstance(item, dict):
            raise ValueError('Series items must be JSON objects')
        item_id, title, after = item.get('id'), item.get('title'), item.get('depends_on', [])
        if (not isinstance(item_id, str) or not ID.fullmatch(item_id) or item_id in data['series']
                or not isinstance(title, str) or not title.strip() or not isinstance(after, list)
                or any(not isinstance(dep, str) or dep not in data['series'] for dep in after)):
            raise ValueError('Each item needs a unique id, title and earlier dependencies')
        data['series'][item_id] = {'title': title, 'depends_on': after, 'status': 'pending'}
    return data['series']


def set_series(store, data, item_id, status, summary, paths, check_id=None):
    items = series_status(store, data['series'], data['checks'])
    if item_id not in items:
        raise ValueError('Unknown series item')
    item = items[item_id]
    if status in ('running', 'complete') and any(items[key]['effective_status'] != 'complete' for key in item['depends_on']):
        raise ValueError('Complete the dependencies first')
    if status in ('complete', 'blocked') and not summary.strip():
        raise ValueError('Completion or blocker evidence is required')
    if status == 'complete':
        item_checks = [check for check in data['checks'] if check.get('item') == item_id]
        evidence = next((check for check in item_checks if check['id'] == check_id), None)
        if (not evidence or item_checks[-1] != evidence or evidence['exit_code'] != 0
                or not evidence['inputs'] or evidence['changed_during_check']
                or changed_paths(store, evidence['inputs'])):
            raise ValueError('A current passing check with watched inputs is required for this item')
        inputs = {**evidence['inputs'], **fingerprint(store, paths)}
    else:
        inputs = fingerprint(store, paths)
    data['series'][item_id] = {'title': item['title'], 'depends_on': item['depends_on'],
                              'status': status, 'summary': summary, 'inputs': inputs,
                              'check_id': check_id,
                              'dependency_checks': {dep: items[dep].get('check_id') for dep in item['depends_on']},
                              'updated_at': now()}
    return data['series'][item_id]


def run_check(store, run_id, phase, command, paths, item=None, reuse=False):
    directory = store.directory(run_id)
    state = store.load(run_id)
    if state['status'] != 'running':
        raise ValueError('Resume the run before checking')
    if not command:
        raise ValueError('Pass the test command after --')
    if item and item not in load_journal(directory)['series']:
        raise ValueError('Unknown series item')
    before = fingerprint(store, paths)
    if reuse and (not before or any(value is None for value in before.values())):
        raise ValueError('Reuse requires a complete watch list of existing files')
    if reuse:
        latest = next((event for event in reversed(load_journal(directory)['checks'])
                       if (event['phase'], event['argv'], event.get('item')) == (phase, command, item)), None)
        if (latest and latest['exit_code'] == 0 and not latest['changed_during_check']
                and latest['inputs'] == before):
            return {**latest, 'reused': True}
    record_id = uuid.uuid4().hex[:12]
    log = directory / f'check-{record_id}.log'
    started = time.monotonic()
    with log.open('xb') as output:
        try:
            process = subprocess.run(command, cwd=store.root, stdout=output, stderr=subprocess.STDOUT)
            code = process.returncode
        except OSError as exc:
            output.write(str(exc).encode())
            code = 127
    after = fingerprint(store, paths)
    event = {'id': record_id, 'phase': phase, 'item': item, 'argv': command,
             'exit_code': code, 'duration_ms': round((time.monotonic() - started)*1000),
             'finished_at': now(), 'log': log.name, 'inputs': before,
             'changed_during_check': [name for name in before if before[name] != after[name]]}
    edit(store, run_id, lambda data: data['checks'].append(event))
    return event


def record_agent(data, thread_id, role, status, model, summary):
    if not ID.fullmatch(thread_id):
        raise ValueError('A real native agent thread ID is required')
    entry = {'thread_id': thread_id, 'role': role, 'status': status, 'model': model,
             'summary': summary, 'recorded_at': now()}
    data['agents'].append(entry)
    return entry


def recovery(store, run_id):
    directory = store.directory(run_id)
    state = store.load(run_id)
    data = load_journal(directory)
    checks = []
    for event in data['checks']:
        checks.append({**event, 'stale_paths': changed_paths(store, event['inputs'])})
    current = {}
    for event in checks:
        key = (event['phase'], tuple(event['argv']), event.get('item'))
        current[key] = event
    stale = sorted({name for check in current.values() if check['exit_code'] == 0 and not check.get('item')
                    for name in check['stale_paths'] + check['changed_during_check']})
    items = series_status(store, data['series'], data['checks'])
    return {'run_id': run_id, 'status': state['status'], 'mode': state['mode'],
            'profile': state.get('policy', {}).get('profile'), 'revision': state['revision'],
            'main_models_at_start': state.get('main_models', {}),
            'stages': state['stages'], 'checks': checks,
            'stale_paths': stale, 'agents': data['agents'], 'series': items,
            'usage': usage_summary(directory)}


def reopen_checks(store, run_id):
    directory = store.directory(run_id)
    with store.lock(directory):
        state = store.load(run_id)
        if state['stages']['IMPLEMENTATION']['status'] != 'completed':
            raise ValueError('There is no completed implementation to recheck')
        for name in ('TESTING', 'REVIEW', 'FINAL_REPORT'):
            stage = state['stages'][name]
            if stage.get('artifact'):
                source = directory / stage['artifact']
                if source.is_symlink():
                    raise ValueError('Artifact cannot be a symlink')
                archive = directory / f"recheck-{state['revision']}-{source.name}"
                if archive.is_symlink():
                    raise ValueError('Recheck archive cannot be a symlink')
                content = source.read_bytes()
                if archive.exists() and archive.read_bytes() != content:
                    raise ValueError('Recheck archive differs; inspect previous artifacts')
                if not archive.exists():
                    atomic_bytes(archive, content)
            state['stages'][name] = {'status': 'pending'}
        state['updated_at'] = now()
        state['status'] = 'running'
        state['revision'] += 1
        prior = load_journal(directory)
        rechecks = prior.setdefault('rechecks', [])
        if not any(item['revision'] == state['revision'] for item in rechecks):
            rechecks.append({'at': now(), 'revision': state['revision']})
        atomic(directory / 'journal.json', prior)
        atomic(directory / 'state.json', state)
    return {'run_id': run_id, 'revision': state['revision'], 'next_stage': 'TESTING'}


def hook_file(store):
    folder = store.root / '.codex'
    file = folder / 'hooks.json'
    if folder.is_symlink() or file.is_symlink():
        raise ValueError('Refusing symlink for hook configuration')
    return file


def usage_handler(store):
    return store.base / 'usage_hook.py'


HOOK_EVENTS = ('PostToolUse', 'PostModelRequest')


def hook_settings(store):
    path = hook_file(store)
    data = read(path) if path.exists() else {}
    if not isinstance(data, dict) or not isinstance(data.get('hooks', {}), dict):
        raise ValueError('Invalid Codex hooks.json')
    hooks = {event: data.get('hooks', {}).get(event, []) for event in HOOK_EVENTS}
    for event, groups in hooks.items():
        if (not isinstance(groups, list)
                or any(not isinstance(group, dict) or not isinstance(group.get('hooks'), list)
                       or any(not isinstance(handler, dict) for handler in group['hooks']) for group in groups)):
            raise ValueError('Invalid hook configuration for ' + event)
    return data, hooks


def own_hook(store, handler):
    try:
        parts = shlex.split(handler.get('command') or '')
    except (TypeError, ValueError):
        return False
    return (len(parts) == 3 and parts[1:] == [str(usage_handler(store)), str(store.root)])


def usage_setup(store, enabled):
    path = hook_file(store)
    data, groups = hook_settings(store)
    updated = {}
    for event, entries in groups.items():
        updated[event] = []
        for group in entries:
            kept = [handler for handler in group['hooks'] if not own_hook(store, handler)]
            if kept or not group['hooks']:
                updated[event].append({**group, 'hooks': kept})
    if enabled:
        store.base.mkdir(exist_ok=True)
        usage = usage_handler(store)
        atomic_bytes(usage, Path(__file__).with_name('usage_hook.py').read_bytes())
        command = shlex.join([sys.executable, str(usage), str(store.root)])
        handler = {'type': 'command', 'command': command}
        updated['PostToolUse'].append({'matcher': 'Bash', 'hooks': [handler]})
        updated['PostModelRequest'].append({'hooks': [handler]})
    if enabled or updated != groups:
        data.setdefault('hooks', {})
        for event, entries in updated.items():
            if entries:
                data['hooks'][event] = entries
            else:
                data['hooks'].pop(event, None)
        path.parent.mkdir(exist_ok=True)
        atomic(path, data)
    # The marker stops collection even if Codex already loaded the hooks.
    marker = store.base / 'usage-enabled.json'
    if enabled or marker.exists():
        atomic(marker, enabled)
    return {'enabled': enabled, 'path': str(path), 'trust_required': enabled}


def usage_status(store):
    _, groups = hook_settings(store)
    marker = store.base / 'usage-enabled.json'
    configured = all(any(own_hook(store, handler) for group in groups[event] for handler in group['hooks'])
                     for event in HOOK_EVENTS)
    return {'configured': configured, 'enabled': configured and marker.is_file() and read(marker) is True,
            'trust_or_client_support': 'requires native runtime confirmation'}


def usage_bind(store, run_id, session=None, turn=None):
    directory = store.directory(run_id)
    if store.load(run_id)['status'] != 'running':
        raise ValueError('Run must be active to bind usage')
    if not usage_status(store)['enabled']:
        return {'bound': False, 'reason': 'Usage hook is disabled'}
    if bool(session) != bool(turn):
        raise ValueError('Pass both native session and turn IDs, or let PostToolUse bind them')
    if session:
        if not ID.fullmatch(session) or not ID.fullmatch(turn):
            raise ValueError('Invalid native session or turn ID')
        bindings = store.base / 'usage-bindings'
        if bindings.is_symlink():
            raise ValueError('Usage bindings cannot be a symlink')
        bindings.mkdir(exist_ok=True)
        from usage_hook import binding_filename
        path = bindings / binding_filename(session, turn)
        if path.is_symlink():
            raise ValueError('Usage binding cannot be a symlink')
        if path.exists() and read(path)['run_id'] != run_id:
            raise ValueError('This Codex turn is already bound to a different run')
        atomic(path, {'run_id': run_id})
        return {'bound': True, 'run_id': run_id, 'coverage': 'partial'}
    atomic(directory / 'usage-binding-request.json', {'requested_at': now()})
    return {'bound': False, 'run_id': run_id, 'reason': 'Waiting for native PostToolUse to link this command'}


def usage_summary(directory):
    events = []
    event_dir = directory / 'usage'
    if not event_dir.is_symlink() and event_dir.is_dir():
        for path in event_dir.glob('*.json'):
            if not path.is_symlink():
                events.append(read(path))
    totals = {}
    for event in events:
        for field, count in event['counts'].items():
            totals[field] = totals.get(field, 0) + count
    by_agent = {}
    for event in events:
        agent = event.get('agent_id') or 'main-or-unidentified'
        models = by_agent.setdefault(agent, {})
        model = models.setdefault(event.get('model') or 'unconfirmed', {})
        for field, count in event['counts'].items():
            model[field] = model.get(field, 0) + count
    return {'source': 'PostModelRequest', 'by_agent_and_model': by_agent, 'coverage': 'partial' if totals else 'unknown',
            'observed_attempts': len(events), 'attempts_with_counts': sum(bool(e['counts']) for e in events),
            'counts': totals, 'models': sorted({e['model'] for e in events if e.get('model')})}


def doctor(store, parent, available, profile, settings):
    policy = config(store.root, profile, settings)
    native_catalog = catalog_snapshot(store.root) if policy['model_selection'] == 'auto' else {}
    resolved = {}
    for role in ROLES:
        try:
            resolved[role] = resolve(policy, role, parent, available, catalog=native_catalog)
        except ValueError as exc:
            resolved[role] = {'error': str(exc)}
    user = user_config_path()
    project = store.base / 'config.json'
    return {'profile': policy['profile'], 'sources': {'built_in': True, 'user': str(user) if user.exists() else None,
             'project': str(project) if project.exists() else None, 'run_override': bool(profile or settings)},
            'profile_source': ('run' if profile else 'project' if project.is_file() and 'profile' in read(project)
                               else 'user' if user.is_file() and 'profile' in read(user) else 'built-in'),
            'models': resolved, 'catalog_candidates': len(native_catalog),
            'delegation': policy['delegation'], 'max_subagents': policy['max_subagents'],
            'native_subagents': 'client tool availability must be checked in the active session',
            'review_policy': policy['review_policy'], 'usage': usage_status(store)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest='op', required=True)
    diagnostics = sub.add_parser('doctor')
    diagnostics.add_argument('--parent')
    diagnostics.add_argument('--available', nargs='+')
    diagnostics.add_argument('--profile', choices=PROFILES)
    diagnostics.add_argument('--set', action='append', default=[])
    for name in ('status', 'recheck'):
        sub.add_parser(name).add_argument('run_id')
    check = sub.add_parser('check')
    check.add_argument('run_id')
    check.add_argument('--phase', choices=('targeted', 'full'), required=True)
    check.add_argument('--watch', action='append', default=[])
    check.add_argument('--item')
    check.add_argument('--reuse', action='store_true')
    agent = sub.add_parser('agent')
    agent.add_argument('run_id')
    agent.add_argument('thread_id')
    agent.add_argument('role', choices=ROLES)
    agent.add_argument('status', choices=('started', 'completed', 'failed', 'cancelled'))
    agent.add_argument('--model')
    agent.add_argument('--summary', default='')
    series = sub.add_parser('series').add_subparsers(dest='action', required=True)
    init = series.add_parser('init')
    init.add_argument('run_id')
    init.add_argument('--file', type=Path, required=True)
    item = series.add_parser('set')
    item.add_argument('run_id')
    item.add_argument('item_id')
    item.add_argument('status', choices=('pending', 'running', 'complete', 'blocked'))
    item.add_argument('--summary', default='')
    item.add_argument('--watch', action='append', default=[])
    item.add_argument('--check-id')
    usage = sub.add_parser('usage').add_subparsers(dest='action', required=True)
    for name in ('enable', 'disable', 'status'):
        usage.add_parser(name)
    bind = usage.add_parser('bind')
    bind.add_argument('run_id')
    bind.add_argument('--session-id')
    bind.add_argument('--turn-id')
    summary = usage.add_parser('show')
    summary.add_argument('run_id')

    arguments = sys.argv[1:]
    if '--' in arguments:
        separator = arguments.index('--')
        arguments, command = arguments[:separator], arguments[separator + 1:]
    else:
        command = []
    args = parser.parse_args(arguments)
    if command and args.op != 'check':
        parser.error('Only check accepts a command after --')
    try:
        store = Store(args.root)
        if args.op == 'doctor':
            result = doctor(store, args.parent, args.available, args.profile, overrides(args.set))
        elif args.op == 'status':
            result = recovery(store, args.run_id)
        elif args.op == 'recheck':
            result = reopen_checks(store, args.run_id)
        elif args.op == 'check':
            result = run_check(store, args.run_id, args.phase, command, args.watch, args.item, args.reuse)
        elif args.op == 'agent':
            result = edit(store, args.run_id, lambda data: record_agent(
                data, args.thread_id, args.role, args.status, args.model, args.summary))
        elif args.op == 'series':
            if args.action == 'init':
                result = edit(store, args.run_id, lambda data: start_series(data, read(args.file)))
            else:
                result = edit(store, args.run_id, lambda data: set_series(
                    store, data, args.item_id, args.status, args.summary, args.watch, args.check_id))
        elif args.action in ('enable', 'disable'):
            result = usage_setup(store, args.action == 'enable')
        elif args.action == 'bind':
            result = usage_bind(store, args.run_id, args.session_id, args.turn_id)
        elif args.action == 'show':
            store.load(args.run_id)
            result = usage_summary(store.directory(args.run_id))
        else:
            result = usage_status(store)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.op == 'check' and result['exit_code']:
            sys.exit(result['exit_code'] if 0 < result['exit_code'] < 126 else 1)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, 'codeflow: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
