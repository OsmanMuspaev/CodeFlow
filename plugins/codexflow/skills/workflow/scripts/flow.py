#!/usr/bin/env python3
"""CodexFlow storage, model policy and native catalog lookup. No model calls."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

STAGES = ('CLASSIFICATION', 'RESEARCH', 'DESIGN', 'PLANNING', 'IMPLEMENTATION', 'TESTING', 'REVIEW', 'FINAL_REPORT')
ARTIFACTS = ('classification.json', 'research.md', 'design.md', 'plan.md', 'implementation-summary.md', 'test-report.md', 'review.md', 'final-report.md')
ROLES = ('orchestrator_model', 'research_model', 'design_model', 'implementation_model', 'review_model')
DEFAULT = Path(__file__).resolve().parents[1] / 'config/default.json'


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def atomic_bytes(path, data):
    if path.is_symlink():
        raise ValueError('Refusing symlink: ' + str(path))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.flow-')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic(path, value):
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


PROFILES = ('efficiency', 'performance', 'speed')
MODES = ('auto', 'quick', 'standard', 'full')
LEGACY_FIELDS = {
    'version', 'allowed_models', 'roles', 'max_subagents', 'report_words',
    'handoff_words', 'soft_token_budget',
}


def user_config_path():
    home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex').expanduser()
    return home / 'codexflow/config.json'


def config_layer(path):
    if path.is_symlink():
        raise ValueError('Configuration cannot be a symlink: ' + str(path))
    if not path.exists():
        return {}
    layer = read(path)
    if not isinstance(layer, dict):
        raise ValueError('Configuration must be a JSON object: ' + str(path))
    # Preserve exact IDs for an existing, complete v1 project file.
    if LEGACY_FIELDS <= layer.keys() and 'profile' not in layer and 'model_selection' not in layer:
        layer['model_selection'] = 'pinned'
    return layer


def merge_config(base, override):
    result = {**base, **override}
    if 'roles' in override:
        roles = override['roles']
        if not isinstance(roles, dict) or any(not isinstance(role, dict) for role in roles.values()):
            raise ValueError('Role overrides must be JSON objects')
        result['roles'] = {
            **base.get('roles', {}),
            **{name: {**base.get('roles', {}).get(name, {}), **role} for name, role in roles.items()},
        }
    return result


def overrides(assignments):
    result = {}
    for assignment in assignments:
        path, separator, raw = assignment.partition('=')
        if not separator:
            raise ValueError('--set expects key=JSON')
        parts = path.split('.')
        if len(parts) == 1 and path != 'profile':
            result[path] = json.loads(raw)
        elif len(parts) == 3 and parts[0] == 'roles' and parts[1] in ROLES:
            roles = result.setdefault('roles', {})
            if not isinstance(roles, dict) or not isinstance(roles.get(parts[1], {}), dict):
                raise ValueError('--set role path conflicts with an earlier override')
            roles.setdefault(parts[1], {})[parts[2]] = json.loads(raw)
        else:
            raise ValueError('Invalid --set path: ' + path)
    return result


def effective_config(global_layer, project_layer, profile=None, settings=None):
    layers = (global_layer, project_layer, settings or {})
    default = read(DEFAULT)
    selected = default['profile'] if profile is None else profile
    for layer in layers:
        if 'profile' in layer and layer['profile'] not in PROFILES:
            raise ValueError('Unknown profile: ' + str(layer['profile']))
        if profile is None:
            selected = layer.get('profile', selected)
    if selected not in PROFILES:
        raise ValueError('Unknown profile: ' + str(selected))
    cfg = merge_config(default, read(DEFAULT.with_name('profiles.json')).get(selected, {}))
    for layer in layers:
        cfg = merge_config(cfg, layer)
    cfg['profile'] = selected
    return validate_config(cfg)


def config(root, profile=None, settings=None, global_only=False):
    global_layer = config_layer(user_config_path())
    project_layer = {} if global_only else config_layer(root / '.codexflow/config.json')
    return effective_config(global_layer, project_layer, profile, settings)


def validate_config(cfg):
    if not isinstance(cfg, dict) or set(cfg) != set(read(DEFAULT)) or type(cfg['version']) is not int or cfg['version'] != 1:
        raise ValueError('Unknown or missing configuration fields/version')
    choices = {
        'profile': PROFILES,
        'model_selection': ('auto', 'pinned'),
        'default_mode': MODES,
        'delegation': ('off', 'selective', 'parallel'),
        'review_policy': ('risk', 'always', 'off'),
    }
    for key, options in choices.items():
        if cfg[key] not in options:
            raise ValueError(f'{key} must be one of: {", ".join(options)}')
    if type(cfg['allow_premium']) is not bool:
        raise ValueError('allow_premium must be boolean')
    if cfg['delegation'] == 'off' and cfg['review_policy'] == 'always':
        raise ValueError('Independent review requires delegation')
    allowed = cfg['allowed_models']
    if not isinstance(allowed, dict):
        raise ValueError('allowed_models must map model IDs to economy, standard or premium')
    for model, tier in allowed.items():
        premium_allowed = cfg['allow_premium'] and tier == 'premium'
        if (model in ('parent', 'stop') or not model.strip()
                or ('astra' in model.casefold() and not premium_allowed)
                or (tier not in ('economy', 'standard') and not premium_allowed)):
            raise ValueError('Forbidden model/tier: ' + model)
    if not isinstance(cfg['roles'], dict) or set(cfg['roles']) != set(ROLES):
        raise ValueError('Exactly five model roles required')
    for role, value in cfg['roles'].items():
        if not isinstance(value, dict) or set(value) != {'model', 'reasoning_effort', 'fallback'}:
            raise ValueError('Invalid role fields: ' + role)
        if value['reasoning_effort'] not in (None, 'low', 'medium', 'high', 'xhigh', 'max'):
            raise ValueError('Invalid reasoning effort')
        for model in (value['model'], value['fallback']):
            if model is not None and not isinstance(model, str):
                raise ValueError('Model/fallback must be a string or null')
            if model not in (None, 'parent', 'stop') and model not in allowed:
                raise ValueError('Model not explicitly allowed: ' + str(model))
        if value['model'] in ('parent', 'stop') or value['fallback'] is None:
            raise ValueError('Invalid model/fallback')
    for key, maximum in (('max_subagents', 2), ('report_words', 10000), ('handoff_words', 10000)):
        if type(cfg[key]) is not int or not 1 <= cfg[key] <= maximum:
            raise ValueError('Invalid ' + key)
    if cfg['soft_token_budget'] is not None and (type(cfg['soft_token_budget']) is not int or cfg['soft_token_budget'] <= 0):
        raise ValueError('Invalid soft_token_budget')
    return cfg


MAIN_ROLES = ('orchestrator_model', 'implementation_model')
FAMILY_TIERS = {'sol': 'standard', 'luna': 'economy', 'astra': 'premium'}


def model_generation(model):
    match = re.fullmatch(r'gpt-([0-9]+(?:\.[0-9]+){0,3})-(sol|luna|astra)', model or '')
    if match is None:
        return None
    numbers = tuple(map(int, match[1].split('.')))
    return match[2], numbers + (0,) * (4 - len(numbers))


def catalog_snapshot(root):
    """Read the native catalog without starting a Codex model session."""
    try:
        result = subprocess.run(
            ['codex', 'debug', 'models'], cwd=root, capture_output=True,
            text=True, check=True, timeout=5, stdin=subprocess.DEVNULL,
        )
        models = json.loads(result.stdout)['models']
        if not isinstance(models, list):
            raise ValueError('Invalid catalog models')
        return {
            row['slug']: sorted({level['effort'] for level in row['supported_reasoning_levels']})
            for row in models
            if row.get('visibility') == 'list' and model_generation(row.get('slug')) is not None
        }
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def newer_in_family(model, baseline, tier):
    candidate = model_generation(model)
    anchor = model_generation(baseline)
    return (candidate is not None and anchor is not None
            and candidate[0] == anchor[0] and FAMILY_TIERS[anchor[0]] == tier
            and candidate[1] >= anchor[1])


def resolve(cfg, role, parent=None, available=None, failed=None, catalog=None):
    """Resolve an authorized request, keeping unknown runtime identity explicit.

    None means the client did not expose evidence. An explicit available list
    is authoritative; never manufacture one from configuration.
    """
    allowed = dict(cfg['allowed_models'])
    value = cfg['roles'][role]
    baseline = value['model']
    auto = cfg.get('model_selection') == 'auto'
    main = role in MAIN_ROLES
    main_baselines = {cfg['roles'][name]['model'] for name in MAIN_ROLES} - {None}
    if auto and len(main_baselines) == 1 and parent not in allowed:
        anchor = next(iter(main_baselines))
        if newer_in_family(parent, anchor, allowed[anchor]):
            allowed[parent] = allowed[anchor]
    if parent is not None and (
        parent not in allowed
        or (allowed[parent] == 'premium' and not cfg.get('allow_premium', False))
        or ('astra' in parent.casefold() and allowed[parent] != 'premium')
    ):
        raise ValueError('Parent model is outside the configured model policy')

    target = baseline or value['fallback']
    source = 'configured'
    supported = catalog or {}
    if auto and baseline is not None:
        if main and len(main_baselines) == 1 and newer_in_family(parent, baseline, allowed[baseline]):
            target = parent
            source = 'current_session'
        elif not main or parent is None:
            effort = value['reasoning_effort']
            candidates = [
                candidate for candidate in supported
                if newer_in_family(candidate, baseline, allowed[baseline])
                and (available is None or candidate in available)
                and (effort is None or effort in supported[candidate])
                and (not main or all(
                    cfg['roles'][name]['reasoning_effort'] is None
                    or cfg['roles'][name]['reasoning_effort'] in supported[candidate]
                    for name in MAIN_ROLES
                ))
            ] if not main or len(main_baselines) == 1 else []
            if candidates:
                target = max(candidates, key=lambda model: (model_generation(model)[1], model))
                allowed[target] = allowed[baseline]
                source = 'catalog'

    def inherited_target(model):
        if model == 'parent':
            if parent is None:
                raise ValueError('Parent fallback needs a known parent ID; configure an explicit allowed model')
            return parent
        return model

    target = inherited_target(target)
    effort = value['reasoning_effort']

    def unavailable(model):
        return (model == failed or (available is not None and model not in available)
                or (not (main and model == parent) and effort is not None
                    and model in supported and effort not in supported[model]))

    if unavailable(target):
        target = inherited_target(value['fallback'])
        source = 'fallback'
    if target == 'stop' or unavailable(target):
        raise ValueError('Model unavailable; no authorized available fallback')
    if target not in allowed:
        raise ValueError('Model must be explicitly allowed')
    if main and parent is not None and target != parent:
        raise ValueError('Main agent cannot switch models; restart with the configured model')
    if main and parent is None:
        if len(main_baselines) > 1:
            raise ValueError('Main roles need a consistent target; the existing session cannot switch models')
    # The target is a requested policy setting, not an attestation of a runtime
    # model. Skills cannot change or introspect the main session themselves.
    return {
        'model': target,
        'requested_model': target,
        'actual_model': parent if main else None,
        'reasoning_effort': value['reasoning_effort'],
        'tier': allowed[target],
        'selection_source': source,
        'inherited': (target == parent) if parent is not None else (None if main else False),
        'availability': ('available' if target in available else 'unavailable') if available is not None else 'unconfirmed',
        'runtime_verification': 'confirmed' if main and parent is not None else 'unconfirmed',
    }


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.base = self.root / '.codexflow'
        for path in (self.base, self.base / 'runs'):
            if path.is_symlink():
                raise ValueError('State directory cannot be a symlink')
        self.runs = self.base / 'runs'

    def directory(self, run_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', run_id):
            raise ValueError('Invalid run ID')
        path = self.runs / run_id
        if path.is_symlink():
            raise ValueError('Run directory cannot be a symlink')
        return path

    @contextmanager
    def lock(self, path):
        lock = path / '.lock'
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.close(fd)
            yield
        finally:
            lock.unlink()

    def start(self, task, mode=None, policy=None, catalog=None):
        policy = config(self.root) if policy is None else policy
        if mode is None:
            mode = policy['default_mode']
        if mode not in MODES:
            raise ValueError('Invalid workflow mode')
        if not task.strip():
            raise ValueError('Empty task')
        self.runs.mkdir(parents=True, exist_ok=True)
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8]
        path = self.directory(run_id)
        path.mkdir(mode=0o700)
        (path / 'task.md').write_text(task + '\n', encoding='utf-8')
        state = {'version': 1, 'run_id': run_id, 'mode': mode, 'status': 'running', 'created_at': now(), 'updated_at': now(), 'revision': 0, 'repair_iterations': 0, 'stages': {s: {'status': 'pending'} for s in STAGES}, 'usage': {'tokens': None, 'source': 'not exposed'}}
        state['policy'] = policy
        if catalog is not None:
            atomic(path / 'model-catalog.json', catalog)
        atomic(path / 'state.json', state)
        return state

    def load(self, run_id):
        path = self.directory(run_id) / 'state.json'
        if path.is_symlink():
            raise ValueError('State cannot be a symlink')
        state = read(path)
        if not isinstance(state, dict) or state.get('version') != 1 or state.get('run_id') != run_id:
            raise ValueError('Invalid state identity/version')
        if state.get('mode') not in ('auto', 'quick', 'standard', 'full') or state.get('status') not in ('running', 'blocked', 'complete'):
            raise ValueError('Invalid state mode/status')
        if type(state.get('revision')) is not int or state['revision'] < 0 or type(state.get('repair_iterations')) is not int or state['repair_iterations'] not in (0, 1):
            raise ValueError('Invalid state counters')
        stages = state.get('stages')
        if not isinstance(stages, dict) or set(stages) != set(STAGES):
            raise ValueError('Invalid state stages')
        for stage, expected in zip(STAGES, ARTIFACTS):
            value = stages[stage]
            if not isinstance(value, dict) or value.get('status') not in ('pending', 'completed', 'skipped', 'blocked'):
                raise ValueError('Invalid stage status')
            if value.get('artifact', expected) != expected:
                raise ValueError('Invalid stage artifact path')
            inline_skip = (value['status'] == 'skipped' and isinstance(value.get('reason'), str)
                           and bool(value['reason'].strip()))
            if value['status'] != 'pending' and 'artifact' not in value and not inline_skip:
                raise ValueError('Missing stage artifact')
        return state

    def checkpoint(self, run_id, stage, status, content, revision):
        return self.record(run_id, [{'stage': stage, 'status': status, 'result': content}], revision,
                           inline_skips=False)

    def record(self, run_id, entries, revision, inline_skips=True):
        if not isinstance(entries, list) or not 1 <= len(entries) <= len(STAGES):
            raise ValueError('Expected a nonempty list of checkpoints')
        path = self.directory(run_id)
        with self.lock(path):
            state = self.load(run_id)
            if state['revision'] != revision:
                raise ValueError('Stale revision; reload state')
            artifacts = []
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != {'stage', 'status', 'result'}:
                    raise ValueError('Each checkpoint needs stage, status and result')
                artifact = self.stage_update(state, path, entry, inline_skips)
                if artifact is not None:
                    artifacts.append(artifact)
            for target, data in artifacts:
                atomic_bytes(target, data)
            # State commits the whole batch; orphaned files after interruption
            # are uncommitted, as with the single-stage checkpoint.
            state['revision'] += 1
            state['updated_at'] = now()
            atomic(path / 'state.json', state)
            return state

    @staticmethod
    def stage_update(state, path, entry, inline_skips):
        stage, status, content = entry['stage'], entry['status'], entry['result']
        if stage not in STAGES or status not in ('completed', 'skipped', 'blocked'):
            raise ValueError('Invalid checkpoint stage or status')
        if isinstance(content, dict) and stage == 'CLASSIFICATION' and status == 'completed':
            content = json.dumps(content, ensure_ascii=False)
        if not isinstance(content, str) or not content.strip():
            raise ValueError('Result or skip reason required')
        index = STAGES.index(stage)
        if any(state['stages'][s]['status'] not in ('completed', 'skipped') for s in STAGES[:index]):
            raise ValueError('Earlier stages are unresolved')
        if state['stages'][stage]['status'] != 'pending':
            raise ValueError('Stage already checkpointed')
        if status == 'skipped' and stage in ('CLASSIFICATION', 'IMPLEMENTATION', 'TESTING', 'FINAL_REPORT'):
            raise ValueError('Mandatory stage')
        if stage == 'REVIEW' and status == 'skipped' and state.get('policy', {}).get('review_policy') == 'always':
            raise ValueError('The saved policy requires independent review')
        if stage == 'CLASSIFICATION' and status == 'completed':
            result = json.loads(content)
            fields = {'mode', 'complexity', 'components', 'architecture_research', 'design_needed', 'parallel_work', 'regression_risk', 'independent_audit', 'change_scope', 'rationale'}
            if not isinstance(result, dict) or set(result) != fields or result['mode'] not in ('quick', 'standard', 'full'):
                raise ValueError('Invalid classification fields/mode')
            for key in ('complexity', 'regression_risk', 'change_scope', 'rationale'):
                if not isinstance(result[key], str) or not result[key].strip():
                    raise ValueError('Classification requires a nonempty ' + key)
            if not isinstance(result['components'], list) or not result['components'] or any(not isinstance(item, str) or not item.strip() for item in result['components']):
                raise ValueError('Classification requires component names/paths')
            research = result['architecture_research']
            if type(research) is not bool and (not isinstance(research, str) or not research.strip()):
                raise ValueError('Classification requires research need/scope')
            for key in ('design_needed', 'parallel_work', 'independent_audit'):
                if type(result[key]) is not bool:
                    raise ValueError('Classification requires boolean ' + key)
            if state['mode'] != 'auto' and state['mode'] != result['mode']:
                raise ValueError('Forced mode must be preserved')
            state['mode'] = result['mode']
        if stage == 'REVIEW' and status == 'completed' and state['stages']['TESTING']['status'] != 'completed':
            raise ValueError('Review requires successful checks')
        if state['status'] != 'running':
            raise ValueError('Run is not active')
        target = path / ARTIFACTS[index]
        if inline_skips and status == 'skipped':
            if stage == 'REVIEW' and state['stages']['CLASSIFICATION'].get('audit_required'):
                raise ValueError('Classification requires independent review')
            state['stages'][stage] = {'status': status, 'reason': content, 'finished_at': now()}
            return None
        if target.is_symlink():
            raise ValueError('Artifact cannot be a symlink')
        update = {'status': status, 'artifact': target.name, 'finished_at': now()}
        if stage == 'CLASSIFICATION' and status == 'completed':
            update['audit_required'] = result['independent_audit']
        state['stages'][stage] = update
        state['status'] = 'blocked' if status == 'blocked' else ('complete' if stage == 'FINAL_REPORT' else 'running')
        return target, content.encode('utf-8')

    def resume(self, run_id):
        path = self.directory(run_id)
        with self.lock(path):
            state = self.load(run_id)
            if state['status'] == 'complete':
                raise ValueError('Run already complete')
            for value in state['stages'].values():
                if value['status'] == 'blocked':
                    value['status'] = 'pending'
            state['revision'] += 1
            state['status'] = 'running'
            state['updated_at'] = now()
            atomic(path / 'state.json', state)
            return state

    def repair(self, run_id):
        path = self.directory(run_id)
        with self.lock(path):
            state = self.load(run_id)
            if state['repair_iterations'] >= 1 or state['status'] == 'complete':
                raise ValueError('Automatic repair limit reached or run complete')
            if state['stages']['TESTING']['status'] == 'pending':
                raise ValueError('Repair requires checks/findings')
            for stage in ('TESTING', 'REVIEW', 'FINAL_REPORT'):
                value = state['stages'][stage]
                if value.get('artifact'):
                    source = path / value['artifact']
                    if source.is_symlink():
                        raise ValueError('Artifact cannot be a symlink')
                    archive = path / ('before-repair-' + source.name)
                    if archive.is_symlink():
                        raise ValueError('Repair archive cannot be a symlink')
                    data = source.read_bytes()
                    if archive.exists():
                        if archive.read_bytes() != data:
                            raise ValueError('Repair archive differs; manual recovery required')
                    else:
                        atomic_bytes(archive, data)
                state['stages'][stage] = {'status': 'pending'}
            state['repair_iterations'] = 1
            state['revision'] += 1
            state['status'] = 'running'
            state['updated_at'] = now()
            atomic(path / 'state.json', state)
            return state

    def history(self):
        if not self.runs.exists():
            return []
        result = []
        for path in sorted(self.runs.iterdir(), reverse=True):
            if path.is_dir() and not path.is_symlink():
                state = self.load(path.name)
                result.append({key: state[key] for key in ('run_id', 'mode', 'status', 'created_at', 'updated_at')})
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--compact', action='store_true', help='Return a small checkpoint summary instead of full state')
    parser.add_argument('--profile', choices=PROFILES)
    parser.add_argument('--set', action='append', default=[], metavar='KEY=JSON')
    parser.add_argument('--run-id', dest='policy_run_id', help='Read the saved run policy for config, catalog or resolve')
    sub = parser.add_subparsers(dest='op', required=True)
    p = sub.add_parser('start'); p.add_argument('--mode', choices=MODES); p.add_argument('--task-file', type=Path, required=True)
    p = sub.add_parser('begin'); p.add_argument('--mode', choices=MODES); p.add_argument('--task-file', type=Path, required=True)
    p.add_argument('--parent'); p.add_argument('--available', nargs='+')
    p = sub.add_parser('record'); p.add_argument('run_id'); p.add_argument('--file', type=Path, required=True); p.add_argument('--revision', type=int, required=True)
    for name in ('status', 'resume', 'repair'):
        p = sub.add_parser(name); p.add_argument('run_id')
    sub.add_parser('history')
    p = sub.add_parser('catalog'); p.add_argument('--output', type=Path, required=True)
    p = sub.add_parser('config'); p.add_argument('--global', dest='global_only', action='store_true')
    mutation = p.add_mutually_exclusive_group()
    mutation.add_argument('--init', action='store_true')
    mutation.add_argument('--save', action='store_true')
    p = sub.add_parser('resolve'); p.add_argument('role', choices=ROLES); p.add_argument('--parent', help='Actual parent ID, only when exposed by the client'); p.add_argument('--available', nargs='+', help='Known available model IDs; omit when not exposed'); p.add_argument('--failed'); p.add_argument('--catalog', type=Path)
    p = sub.add_parser('checkpoint'); p.add_argument('run_id'); p.add_argument('stage', choices=STAGES); p.add_argument('status', choices=('completed', 'skipped', 'blocked')); p.add_argument('--file', type=Path, required=True); p.add_argument('--revision', type=int, required=True)
    args = parser.parse_args()
    try:
        store = Store(args.root)
        settings = overrides(args.set)
        global_only = getattr(args, 'global_only', False)
        mutation = getattr(args, 'init', False) or getattr(args, 'save', False)
        if (args.profile or settings) and args.op not in ('start', 'begin', 'config', 'catalog', 'resolve'):
            raise ValueError('--profile and --set apply to start, begin, config, catalog or resolve')
        if args.policy_run_id and (
            args.op not in ('config', 'catalog', 'resolve') or args.profile or settings or global_only or mutation
        ):
            raise ValueError('--run-id reads a saved policy without overrides')
        if args.op == 'start':
            result = store.start(args.task_file.read_text(encoding='utf-8'), args.mode,
                                 config(store.root, args.profile, settings))
        elif args.op == 'begin':
            policy = config(store.root, args.profile, settings)
            catalog = catalog_snapshot(store.root) if policy['model_selection'] == 'auto' else {}
            main_models = {role: resolve(policy, role, args.parent, args.available, catalog=catalog)
                           for role in MAIN_ROLES}
            if len({model['requested_model'] for model in main_models.values()}) != 1:
                raise ValueError('Main roles need the same session model')
            result = store.start(args.task_file.read_text(encoding='utf-8'), args.mode, policy, catalog)
        elif args.op == 'record':
            result = store.record(args.run_id, read(args.file), args.revision)
        elif args.op == 'history': result = store.history()
        elif args.op == 'status': result = store.load(args.run_id)
        elif args.op in ('resume', 'repair'): result = getattr(store, args.op)(args.run_id)
        elif args.op == 'checkpoint': result = store.checkpoint(args.run_id, args.stage, args.status, args.file.read_text(encoding='utf-8'), args.revision)
        else:
            saved = store.load(args.policy_run_id).get('policy') if args.policy_run_id else None
            policy = (config(store.root, args.profile, settings, global_only)
                      if saved is None else validate_config(saved))
            if args.op == 'catalog':
                models = catalog_snapshot(store.root) if policy['model_selection'] == 'auto' else {}
                atomic(args.output, models)
                result = {'catalog_candidates': len(models), 'snapshot': str(args.output)}
            elif args.op == 'resolve':
                snapshot = args.catalog
                if snapshot is None and args.policy_run_id:
                    saved_catalog = store.directory(args.policy_run_id) / 'model-catalog.json'
                    if saved_catalog.is_symlink():
                        raise ValueError('Catalog cannot be a symlink')
                    if saved_catalog.exists():
                        snapshot = saved_catalog
                catalog = read(snapshot) if snapshot else None
                result = resolve(policy, args.role, args.parent, args.available, args.failed, catalog)
            else:
                result = policy
                if mutation:
                    path = user_config_path() if global_only else store.base / 'config.json'
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if args.init:
                        initial = {**settings}
                        if args.profile is not None:
                            initial['profile'] = args.profile
                        with path.open('x', encoding='utf-8') as file:
                            json.dump(initial, file, indent=2)
                            file.write('\n')
                    else:
                        if not settings and args.profile is None:
                            raise ValueError('config --save requires --profile or --set')
                        delta = {**settings}
                        if args.profile is not None:
                            delta['profile'] = args.profile
                        atomic(path, merge_config(config_layer(path), delta))
        if args.compact and isinstance(result, dict) and 'run_id' in result:
            state = result
            result = {key: state[key] for key in ('run_id', 'mode', 'status', 'revision', 'repair_iterations')}
            result['next_stage'] = next((stage for stage in STAGES if state['stages'][stage]['status'] in ('pending', 'blocked')), None)
            result['resolved_stages'] = [stage for stage in STAGES if state['stages'][stage]['status'] in ('completed', 'skipped')]
        if args.op == 'begin':
            result['profile'] = policy['profile']
            result['main_models'] = main_models
            result['workflow'] = {key: policy[key] for key in (
                'delegation', 'max_subagents', 'review_policy', 'soft_token_budget',
                'report_words', 'handoff_words',
            )}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, 'codexflow: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
