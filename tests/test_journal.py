import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'plugins/codeflow/skills/workflow/scripts'
sys.path.insert(0, str(SCRIPTS))
try:
    journal = importlib.import_module('journal')
    hook = importlib.import_module('usage_hook')
    flow = importlib.import_module('flow')
finally:
    sys.path.remove(str(SCRIPTS))


class JournalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.store = journal.Store(self.root)
        environment = patch.dict(os.environ, {'CODEX_HOME': str(self.root / 'home')})
        environment.start()
        self.addCleanup(environment.stop)
        self.state = self.store.start('Check an example', 'quick')
        self.run_id = self.state['run_id']
        self.work = self.root / 'source.py'
        self.work.write_text('initial')
        self.command = [sys.executable, '-c', 'print("checked")']

    def check(self, item=None, reuse=False):
        return journal.run_check(self.store, self.run_id, 'targeted', self.command,
                                 ['source.py'], item, reuse)

    def item(self, key, status, evidence=None):
        return journal.edit(self.store, self.run_id, lambda data: journal.set_series(
            self.store, data, key, status, 'check result', [], evidence))

    def test_check_logs_real_result_detects_drift_and_reuses_matching_inputs(self):
        result = self.check()
        folder = self.store.directory(self.run_id)
        self.assertEqual((folder / result['log']).read_text().strip(), 'checked')
        self.assertEqual(self.check(reuse=True)['id'], result['id'])
        (self.root / 'README.md').write_text('unrelated')
        self.assertEqual(self.check(reuse=True)['id'], result['id'])
        self.work.write_text('changed')
        self.assertEqual(journal.recovery(self.store, self.run_id)['stale_paths'], ['source.py'])
        self.assertNotEqual(self.check(reuse=True)['id'], result['id'])
        self.assertEqual(journal.recovery(self.store, self.run_id)['stale_paths'], [])

    def test_failed_check_records_the_real_exit_code(self):
        result = journal.run_check(self.store, self.run_id, 'full',
                                   [sys.executable, '-c', 'raise SystemExit(7)'], ['source.py'])
        self.assertEqual(result['exit_code'], 7)
        self.assertEqual(journal.recovery(self.store, self.run_id)['checks'][0]['exit_code'], 7)

    def test_series_keeps_independent_evidence_and_invalidates_dependents(self):
        journal.edit(self.store, self.run_id, lambda data: journal.start_series(data, [
            {'id': 'a', 'title': 'A'},
            {'id': 'b', 'title': 'B', 'depends_on': ['a']},
            {'id': 'c', 'title': 'C'},
        ]))
        with self.assertRaises(ValueError):
            self.item('b', 'running')
        with self.assertRaises(ValueError):
            self.item('a', 'complete')
        a = self.check('a')
        self.item('a', 'complete', a['id'])
        independent = self.root / 'other.py'
        independent.write_text('independent')
        for key in ('b', 'c'):
            check = journal.run_check(self.store, self.run_id, 'targeted', self.command, ['other.py'], key)
            self.item(key, 'complete', check['id'])
        self.work.write_text('change')
        states = journal.recovery(self.store, self.run_id)['series']
        self.assertEqual([states[key]['effective_status'] for key in ('a', 'b', 'c')],
                         ['stale', 'stale', 'complete'])
        self.item('a', 'complete', self.check('a')['id'])
        self.assertEqual(journal.recovery(self.store, self.run_id)['series']['b']['effective_status'], 'stale')

    def test_run_cannot_complete_while_a_series_item_is_pending(self):
        journal.edit(self.store, self.run_id, lambda data: journal.start_series(
            data, [{'id': 'a', 'title': 'Pending result'}]))
        state = self.state
        directory = self.store.directory(self.run_id)
        for stage, artifact in zip(flow.STAGES[:-1], flow.ARTIFACTS[:-1]):
            state['stages'][stage] = {'status': 'completed', 'artifact': artifact}
        journal.atomic(directory / 'state.json', state)
        with self.assertRaisesRegex(ValueError, 'all series items'):
            self.store.checkpoint(self.run_id, 'FINAL_REPORT', 'completed', 'Done', 0)
        self.assertFalse((directory / 'final-report.md').exists())
        self.assertEqual(self.store.load(self.run_id)['status'], 'running')

    def test_recheck_archives_results_and_keeps_repair_count(self):
        state = self.state
        state['stages']['IMPLEMENTATION'] = {'status': 'completed', 'artifact': 'implementation-summary.md'}
        state['stages']['TESTING'] = {'status': 'completed', 'artifact': 'test-report.md'}
        state['repair_iterations'] = 1
        directory = self.store.directory(self.run_id)
        (directory / 'test-report.md').write_text('verified before change')
        journal.atomic(directory / 'state.json', state)
        journal.reopen_checks(self.store, self.run_id)
        state = self.store.load(self.run_id)
        self.assertEqual(state['stages']['TESTING']['status'], 'pending')
        self.assertEqual(state['stages']['IMPLEMENTATION']['status'], 'completed')
        self.assertEqual(state['repair_iterations'], 1)
        self.assertEqual((directory / 'recheck-0-test-report.md').read_text(), 'verified before change')

    def test_usage_only_for_linked_turn_and_preserves_existing_hook(self):
        folder = self.root / '.codex'
        folder.mkdir()
        existing = {'hooks': {'PostToolUse': [{'matcher': 'Edit', 'hooks': [
            {'type': 'command', 'command': 'existing-hook'}
        ]}]}}
        journal.atomic(folder / 'hooks.json', existing)
        journal.usage_setup(self.store, True)
        journal.usage_setup(self.store, True)
        self.assertTrue(journal.usage_status(self.store)['enabled'])
        payload = {'session_id': 'session1', 'turn_id': 'turn1', 'model': 'gpt-6.2-sol',
                   'hook_event_name': 'PostModelRequest',
                   'token_usage': {'input_tokens': 8, 'output_tokens': 2},
                   'prompt': 'must never be written'}
        self.assertFalse(journal.usage_bind(self.store, self.run_id)['bound'])
        subprocess.run([sys.executable, str(journal.usage_handler(self.store)), str(self.root)],
                       input=json.dumps({**payload, 'hook_event_name': 'PostToolUse',
                           'tool_input': {'command': f'python3 journal.py usage bind {self.run_id}'}}),
                       text=True, check=True)
        hook.collect(self.root, payload)
        subprocess.run([sys.executable, str(journal.usage_handler(self.store)), str(self.root)],
                       input=json.dumps({**payload, 'turn_id': 'unrelated'}), text=True, check=True)
        directory = self.store.directory(self.run_id)
        summary = journal.usage_summary(directory)
        self.assertEqual(summary['observed_attempts'], 1)
        self.assertEqual(summary['counts'], {'input_tokens': 8, 'output_tokens': 2})
        self.assertEqual(summary['coverage'], 'partial')
        [event] = (directory / 'usage').glob('*.json')
        self.assertNotIn('prompt', json.loads(event.read_text()))
        journal.usage_setup(self.store, False)
        self.assertFalse(journal.usage_status(self.store)['enabled'])
        self.assertEqual(journal.read(folder / 'hooks.json'), existing)

    def test_doctor_pinned_config_does_not_require_catalog_or_create_run(self):
        result = journal.doctor(self.store, 'gpt-6.2-sol', None, 'efficiency', {'model_selection': 'pinned'})
        self.assertEqual(result['profile_source'], 'run')
        self.assertEqual(result['models']['orchestrator_model']['actual_model'], 'gpt-6.2-sol')
        self.assertEqual(len(self.store.history()), 1)
