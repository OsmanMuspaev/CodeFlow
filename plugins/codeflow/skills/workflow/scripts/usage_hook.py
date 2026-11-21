#!/usr/bin/env python3
"""Store only numeric usage from a local PostModelRequest hook."""
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
import uuid


def object_at(path):
    if path.is_symlink():
        raise ValueError('Unexpected symlink')
    return json.loads(path.read_text(encoding='utf-8'))


def atomic(path, data):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.usage-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def binding_filename(session, turn):
    digest = hashlib.sha256(f'{session}:{turn}'.encode()).hexdigest()
    return f'{digest}.json'


def collect(root, event):
    kind = event.get('hook_event_name')
    if kind not in ('PostToolUse', 'PostModelRequest'):
        return
    session, turn = event.get('session_id'), event.get('turn_id')
    if not all(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value)
               for value in (session, turn)):
        return
    base = root / '.codexflow'
    binding_dir = base / 'usage-bindings'
    runs = base / 'runs'
    if any(path.is_symlink() for path in (base, binding_dir, runs)):
        return
    if object_at(base / 'usage-enabled.json') is not True:
        return
    binding_path = binding_dir / binding_filename(session, turn)
    if kind == 'PostToolUse':
        # Match only a simple, dedicated invocation. A successful helper call
        # must also have written the fresh local request inside this run.
        command = shlex.split(event.get('tool_input', {}).get('command', ''))
        if (len(command) < 5 or Path(command[1]).name != 'journal.py'
                or command[-3:-1] != ['usage', 'bind']):
            return
        run_id = command[-1]
    else:
        run_id = object_at(binding_path)['run_id']
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', run_id):
        return
    directory = runs / run_id
    if directory.is_symlink() or object_at(directory / 'state.json')['status'] != 'running':
        return
    if kind == 'PostToolUse':
        requested = object_at(directory / 'usage-binding-request.json')['requested_at']
        elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(requested)
        if 0 <= elapsed.total_seconds() <= 30:
            binding_dir.mkdir(mode=0o700, exist_ok=True)
            with binding_path.open('x', encoding='utf-8') as stream:
                json.dump({'run_id': run_id}, stream)
        return
    event_dir = directory / 'usage'
    if event_dir.is_symlink():
        return
    event_dir.mkdir(mode=0o700, exist_ok=True)
    fields = {
        'input_tokens': ('input_tokens', 'inputTokens'),
        'cached_input_tokens': ('cached_input_tokens', 'cachedInputTokens'),
        'output_tokens': ('output_tokens', 'outputTokens'),
        'reasoning_output_tokens': ('reasoning_output_tokens', 'reasoningOutputTokens', 'reasoning_tokens'),
        'total_tokens': ('total_tokens', 'totalTokens'),
    }
    usage = event.get('token_usage') or {}
    if not isinstance(usage, dict):
        usage = {}
    counts = {}
    for name, aliases in fields.items():
        value = next((usage[key] for key in aliases if key in usage), None)
        if type(value) is int and value >= 0:
            counts[name] = value
    model = event.get('model')
    agent = event.get('agent_id')
    model = model if isinstance(model, str) and re.fullmatch(r'[\w./:-]{1,100}', model) else None
    agent = agent if isinstance(agent, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', agent) else None
    atomic(event_dir / f'{uuid.uuid4().hex}.json',
           {'turn_id': turn, 'model': model, 'agent_id': agent, 'counts': counts})


if __name__ == '__main__':
    try:
        collect(Path(sys.argv[1]).resolve(), json.load(sys.stdin))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError):
        pass  # Optional local telemetry never blocks a Codex request.
