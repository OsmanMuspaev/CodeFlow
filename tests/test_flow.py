import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'plugins/codeflow/skills/workflow'
spec = importlib.util.spec_from_file_location('flow', SKILL / 'scripts/flow.py')
flow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flow)


def classification(mode):
    return json.dumps(dict(mode=mode, complexity='local' if mode == 'quick' else 'multi-component', components=['example'], architecture_research=mode != 'quick', design_needed=mode != 'quick', parallel_work=False, regression_risk='low', independent_audit=False, change_scope='small', rationale='Examined example function and its test.'))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = flow.Store(self.root)

    def checkpoint(self, state, stage, content='Result', status='completed'):
        return self.store.checkpoint(state['run_id'], stage, status, content, state['revision'])

    def test_modes_and_durable_reload(self):
        for mode in ('quick', 'standard', 'full'):
            with self.subTest(mode=mode):
                state = self.store.start('Change function', mode)
                state = self.checkpoint(state, 'CLASSIFICATION', classification(mode))
                for stage in flow.STAGES[1:]:
                    optional = stage in ('RESEARCH', 'DESIGN', 'PLANNING', 'REVIEW')
                    state = self.checkpoint(state, stage, 'Local change; not needed' if optional and mode == 'quick' else 'Verified result', 'skipped' if optional and mode == 'quick' else 'completed')
                self.assertEqual(flow.Store(self.root).load(state['run_id'])['status'], 'complete')
                self.assertTrue(all((self.store.directory(state['run_id']) / name).exists() for name in flow.ARTIFACTS))
        self.assertEqual(len(self.store.history()), 3)

    def test_batch_validates_before_writing_and_resumes_after_failed_tests(self):
        initial = self.store.start('Fix example', 'quick')
        run_id = initial['run_id']

        def entry(stage, result, status='completed'):
            return {'stage': stage, 'status': status, 'result': result}

        classified = entry('CLASSIFICATION', json.loads(classification('quick')))
        prerequisites = [classified] + [
            entry(stage, 'Local correction', 'skipped') for stage in ('RESEARCH', 'DESIGN', 'PLANNING')
        ]
        with self.assertRaisesRegex(ValueError, 'Earlier stages'):
            self.store.record(run_id, [classified, entry('TESTING', 'Passed')], 0)
        self.assertEqual(self.store.load(run_id)['revision'], 0)
        self.assertFalse((self.store.directory(run_id) / 'classification.json').exists())
        state = self.store.record(run_id, prerequisites, 0)
        self.assertEqual(state['revision'], 1)
        self.assertEqual(state['stages']['RESEARCH']['reason'], 'Local correction')
        self.assertFalse((self.store.directory(run_id) / 'research.md').exists())
        changes = entry('IMPLEMENTATION', 'Corrected behavior')
        failed = entry('TESTING', 'python3 -m unittest exited 1', 'blocked')
        with self.assertRaises(ValueError):
            self.store.record(run_id, [changes, failed, entry('FINAL_REPORT', 'Done')], 1)
        self.assertFalse((self.store.directory(run_id) / 'implementation-summary.md').exists())
        state = self.store.record(run_id, [changes, failed], 1)
        self.assertEqual(state['status'], 'blocked')
        state = self.store.repair(run_id)
        self.assertTrue((self.store.directory(run_id) / 'before-repair-test-report.md').exists())
        with self.assertRaisesRegex(ValueError, 'Stale revision'):
            self.store.record(run_id, [entry('TESTING', 'Passed')], 1)
        done = self.store.record(run_id, [
            entry('TESTING', 'python3 -m unittest exited 0'),
            entry('REVIEW', 'Low risk and covered by tests', 'skipped'),
            entry('FINAL_REPORT', 'Completed'),
        ], state['revision'])
        self.assertEqual(self.store.load(run_id)['status'], 'complete')
        self.assertEqual(done['repair_iterations'], 1)

    def test_auto_classification_and_forced_mode(self):
        state = self.store.start('Task', 'auto')
        self.assertEqual(self.checkpoint(state, 'CLASSIFICATION', classification('standard'))['mode'], 'standard')
        state = self.store.start('Task', 'quick')
        with self.assertRaises(ValueError): self.checkpoint(state, 'CLASSIFICATION', classification('full'))

    def test_invalid_classification_types_do_not_advance_state(self):
        state = self.store.start('Task', 'auto')
        for key, value in (('components', 'one'), ('complexity', None), ('independent_audit', 'false')):
            data = json.loads(classification('quick')); data[key] = value
            with self.assertRaises(ValueError): self.checkpoint(state, 'CLASSIFICATION', json.dumps(data))
            self.assertEqual(self.store.load(state['run_id'])['revision'], 0)

    def test_order_revision_skip_and_empty(self):
        state = self.store.start('Task', 'quick')
        with self.assertRaises(ValueError): self.checkpoint(state, 'IMPLEMENTATION')
        with self.assertRaises(ValueError): self.checkpoint(state, 'CLASSIFICATION', status='skipped')
        with self.assertRaises(ValueError): self.checkpoint(state, 'CLASSIFICATION', '')
        new = self.checkpoint(state, 'CLASSIFICATION', classification('quick'))
        with self.assertRaises(ValueError): self.store.checkpoint(state['run_id'], 'RESEARCH', 'skipped', 'Known', state['revision'])
        self.assertEqual(new['revision'], 1)

    def to_testing(self):
        state = self.store.start('Task', 'standard')
        state = self.checkpoint(state, 'CLASSIFICATION', classification('standard'))
        for stage in flow.STAGES[1:5]: state = self.checkpoint(state, stage)
        return state

    def test_block_resume_and_single_repair(self):
        state = self.to_testing()
        state = self.checkpoint(state, 'TESTING', 'exit 1: assertion failed', 'blocked')
        with self.assertRaises(ValueError): self.checkpoint(state, 'REVIEW')
        state = self.store.repair(state['run_id'])
        self.assertEqual(state['repair_iterations'], 1)
        archive = self.store.directory(state['run_id']) / 'before-repair-test-report.md'
        self.assertIn('exit 1', archive.read_text())
        state = self.checkpoint(state, 'TESTING', 'exit 1: still failing', 'blocked')
        with self.assertRaises(ValueError): self.store.repair(state['run_id'])
        resumed = self.store.resume(state['run_id'])
        self.assertEqual(resumed['stages']['TESTING']['status'], 'pending')
        self.assertEqual(resumed['repair_iterations'], 1)

    def test_required_review_cannot_be_skipped(self):
        policy = flow.effective_config({}, {}, profile='performance')
        state = self.store.start('Task', 'quick', policy)
        state = self.checkpoint(state, 'CLASSIFICATION', classification('quick'))
        for stage in ('RESEARCH', 'DESIGN', 'PLANNING'):
            state = self.checkpoint(state, stage, 'Not needed', 'skipped')
        state = self.checkpoint(state, 'IMPLEMENTATION')
        state = self.checkpoint(state, 'TESTING')
        with self.assertRaisesRegex(ValueError, 'requires independent review'):
            self.checkpoint(state, 'REVIEW', 'Skip', 'skipped')
        state = self.checkpoint(state, 'REVIEW', 'Reviewer unavailable', 'blocked')
        self.assertEqual(state['status'], 'blocked')

    def test_interrupted_repair_is_idempotent(self):
        state = self.to_testing()
        state = self.checkpoint(state, 'TESTING', 'failed check', 'blocked')
        directory = self.store.directory(state['run_id'])
        (directory / 'before-repair-test-report.md').write_text('failed check')
        repaired = self.store.repair(state['run_id'])
        self.assertEqual(repaired['repair_iterations'], 1)
        self.assertEqual(repaired['stages']['TESTING']['status'], 'pending')

    def test_recover_uncommitted_artifact(self):
        state = self.store.start('Task', 'quick')
        artifact = self.store.directory(state['run_id']) / 'classification.json'
        artifact.write_text(classification('quick'))
        resumed = self.store.resume(state['run_id'])
        self.assertEqual(resumed['stages']['CLASSIFICATION']['status'], 'pending')
        self.checkpoint(resumed, 'CLASSIFICATION', artifact.read_text())

    def test_tampered_artifact_path_is_rejected(self):
        state = self.to_testing()
        state = self.checkpoint(state, 'TESTING', 'failed check', 'blocked')
        state_file = self.store.directory(state['run_id']) / 'state.json'
        outside = self.root / 'private.txt'; outside.write_text('outside data')
        for target in (str(outside), '../private.txt'):
            altered = copy.deepcopy(state)
            altered['stages']['TESTING']['artifact'] = target
            state_file.write_text(json.dumps(altered))
            with self.assertRaises(ValueError): self.store.repair(state['run_id'])
        self.assertEqual(outside.read_text(), 'outside data')
        self.assertFalse((state_file.parent / 'before-repair-private.txt').exists())

    def test_traversal_symlinks_and_writer_lock(self):
        for run_id in ('../escape', '/tmp/run', '.', 'a/b'):
            with self.assertRaises(ValueError): self.store.load(run_id)
        state = self.store.start('Task', 'quick')
        directory = self.store.directory(state['run_id'])
        target = self.root / 'untouched'; target.write_text('safe')
        (directory / 'classification.json').symlink_to(target)
        with self.assertRaises(ValueError): self.checkpoint(state, 'CLASSIFICATION', classification('quick'))
        self.assertEqual(target.read_text(), 'safe')
        (directory / '.lock').write_text('other writer')
        with self.assertRaises(FileExistsError): self.store.resume(state['run_id'])


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); (self.root / '.codexflow').mkdir()
        self.cfg = flow.read(flow.DEFAULT)
        self.cfg['allowed_models'] = {'test-standard': 'standard', 'test-economy': 'economy'}
        for role in self.cfg['roles'].values():
            role.update(model=None, fallback='parent')

    def validate(self, cfg):
        (self.root / '.codexflow/config.json').write_text(json.dumps(cfg))
        return flow.config(self.root)

    def test_unknown_parent_still_rejected(self):
        with self.assertRaises(ValueError): flow.resolve(flow.read(flow.DEFAULT), 'orchestrator_model', 'unknown', ['unknown'])

    def test_bundled_defaults_resolve_all_roles(self):
        cfg = flow.config(self.root)
        self.assertEqual(cfg['allowed_models'], {'gpt-6.2-sol': 'standard', 'gpt-6-luna': 'economy'})
        for role in flow.ROLES:
            expected = 'gpt-6.2-sol' if role in ('orchestrator_model', 'implementation_model') else 'gpt-6-luna'
            with self.subTest(role=role):
                selected = flow.resolve(cfg, role, 'gpt-6.2-sol', ['gpt-6.2-sol', 'gpt-6-luna'])
                self.assertEqual(selected['model'], expected)
                self.assertEqual(cfg['roles'][role]['fallback'], 'stop')
        for role in ('research_model', 'design_model', 'review_model'):
            with self.subTest(unavailable=role), self.assertRaises(ValueError):
                flow.resolve(cfg, role, 'gpt-6.2-sol', ['gpt-6.2-sol'])
        with self.assertRaises(ValueError):
            flow.resolve(cfg, 'orchestrator_model', 'gpt-6-luna', ['gpt-6.2-sol', 'gpt-6-luna'])

    def test_missing_runtime_identity_does_not_block_explicit_defaults(self):
        cfg = flow.config(self.root)
        for role in flow.ROLES:
            with self.subTest(role=role):
                selected = flow.resolve(cfg, role)
                self.assertEqual(selected['requested_model'], cfg['roles'][role]['model'])
                self.assertIsNone(selected['actual_model'])
                self.assertEqual(selected['runtime_verification'], 'unconfirmed')
                self.assertEqual(selected['availability'], 'unconfirmed')

    def test_known_parent_is_confirmed_and_forbidden_parent_rejected(self):
        cfg = flow.config(self.root)
        selected = flow.resolve(cfg, 'orchestrator_model', 'gpt-6.2-sol')
        self.assertEqual(selected['actual_model'], 'gpt-6.2-sol')
        self.assertEqual(selected['runtime_verification'], 'confirmed')
        for parent in ('gpt-6-astra', 'unapproved-model', 'gpt-6-luna'):
            with self.subTest(parent=parent), self.assertRaises(ValueError):
                flow.resolve(cfg, 'orchestrator_model', parent)

    def test_missing_identity_does_not_bypass_availability_or_failure(self):
        cfg = flow.config(self.root)
        with self.assertRaises(ValueError):
            flow.resolve(cfg, 'review_model', available=['gpt-6.2-sol'])
        with self.assertRaises(ValueError):
            flow.resolve(cfg, 'research_model', failed='gpt-6-luna')
        with self.assertRaises(ValueError):
            flow.resolve(cfg, 'implementation_model', available=[])
        selected = flow.resolve(cfg, 'review_model', available=['gpt-6-luna'])
        self.assertEqual(selected['availability'], 'available')
        self.assertIsNone(selected['actual_model'])

    def test_missing_identity_cannot_guess_parent_fallback(self):
        cfg = self.validate(self.cfg)
        with self.assertRaises(ValueError): flow.resolve(cfg, 'orchestrator_model')
        self.cfg['roles']['research_model'].update(model='test-economy', fallback='parent')
        with self.assertRaises(ValueError):
            flow.resolve(self.validate(self.cfg), 'research_model', failed='test-economy')

    def test_missing_identity_cannot_bypass_empty_allowlist(self):
        self.cfg['allowed_models'] = {}
        with self.assertRaises(ValueError):
            flow.resolve(self.validate(self.cfg), 'orchestrator_model')

    def test_main_targets_must_be_consistent_without_identity(self):
        cfg = flow.config(self.root)
        cfg['roles']['implementation_model']['model'] = 'gpt-6-luna'
        with self.assertRaises(ValueError): flow.resolve(cfg, 'orchestrator_model')

    def test_auto_selects_latest_compatible_generation_from_catalog(self):
        cfg = flow.config(self.root)
        catalog = {
            'gpt-6.9-sol': ['medium'],
            'gpt-6.10-sol': ['medium'],
            'gpt-6.11-sol': ['high'],
            'gpt-99-astra': ['medium'],
            'gpt-99-luna-preview': ['medium'],
            'gpt-7-luna': ['low', 'medium'],
        }
        main = flow.resolve(cfg, 'orchestrator_model', catalog=catalog)
        self.assertEqual(main['requested_model'], 'gpt-6.10-sol')
        self.assertIsNone(main['actual_model'])
        self.assertEqual(main['availability'], 'unconfirmed')
        self.assertEqual(main['selection_source'], 'catalog')
        child = flow.resolve(cfg, 'review_model', parent='gpt-6.10-sol', catalog=catalog)
        self.assertEqual(child['requested_model'], 'gpt-7-luna')
        self.assertEqual(child['tier'], 'economy')
        limited = flow.resolve(cfg, 'implementation_model', available=['gpt-6.9-sol'], catalog=catalog)
        self.assertEqual(limited['requested_model'], 'gpt-6.9-sol')
        with self.assertRaises(ValueError):
            flow.resolve(cfg, 'review_model', failed='gpt-7-luna', catalog=catalog)
        with self.assertRaises(ValueError):
            flow.resolve(cfg, 'orchestrator_model', parent='gpt-99-astra', catalog=catalog)

    def test_auto_preserves_current_eligible_sol_and_configured_baseline_without_catalog(self):
        cfg = flow.config(self.root)
        self.assertEqual(
            flow.resolve(cfg, 'implementation_model', parent='gpt-6.2-sol',
                         catalog={'gpt-7-sol': ['medium']})['requested_model'],
            'gpt-6.2-sol',
        )
        self.assertEqual(flow.resolve(cfg, 'implementation_model', catalog={})['requested_model'],
                         'gpt-6.2-sol')

    def test_legacy_project_config_keeps_exact_model(self):
        cfg = flow.config(self.root)
        cfg['allowed_models']['gpt-6.1-sol'] = cfg['allowed_models'].pop('gpt-6.2-sol')
        for role in flow.MAIN_ROLES:
            cfg['roles'][role]['model'] = 'gpt-6.1-sol'
        cfg = self.validate({key: cfg[key] for key in flow.LEGACY_FIELDS})
        selected = flow.resolve(cfg, 'implementation_model', catalog={'gpt-6.2-sol': ['medium']})
        self.assertEqual(selected['requested_model'], 'gpt-6.1-sol')
        with self.assertRaises(ValueError):
            flow.resolve(cfg, 'implementation_model', parent='gpt-6.2-sol')

    def test_project_config_keeps_precedence(self):
        self.cfg['roles']['review_model'].update(model='test-economy', fallback='stop')
        path = self.root / '.codexflow/config.json'
        path.write_text(json.dumps(self.cfg))
        before = path.read_bytes()
        effective = flow.config(self.root)
        self.assertEqual(effective, self.cfg)
        self.assertEqual(path.read_bytes(), before)

    def test_existing_empty_allowlist_is_not_silently_replaced(self):
        self.cfg['allowed_models'] = {}
        effective = self.validate(self.cfg)
        self.assertEqual(effective['allowed_models'], {})
        with self.assertRaises(ValueError):
            flow.resolve(effective, 'orchestrator_model', 'gpt-6.2-sol', ['gpt-6.2-sol'])

    def test_premium_profile_controls_astra_upgrade_and_fallback(self):
        catalog = {'gpt-7-astra': ['xhigh'], 'gpt-7-sol': ['medium'], 'gpt-6.2-sol': ['high']}
        cfg = flow.effective_config({}, {}, profile='performance')
        self.assertEqual(flow.resolve(cfg, 'orchestrator_model', catalog=catalog)['requested_model'],
                         'gpt-7-astra')
        self.assertEqual(flow.resolve(cfg, 'review_model', parent='gpt-7-astra', catalog=catalog)['requested_model'],
                         'gpt-6.2-sol')
        with self.assertRaises(ValueError):
            flow.resolve(cfg, 'orchestrator_model', catalog=catalog, failed='gpt-7-astra')
        default = flow.effective_config({}, {})
        self.assertEqual(flow.resolve(default, 'orchestrator_model', catalog=catalog)['requested_model'],
                         'gpt-7-sol')
        with self.assertRaises(ValueError):
            flow.resolve(default, 'orchestrator_model', parent='gpt-7-astra')
        with self.assertRaisesRegex(ValueError, 'requires delegation'):
            flow.effective_config({}, {}, profile='performance', settings={'delegation': 'off'})

    def test_parent_and_explicit_economy(self):
        cfg = self.validate(self.cfg)
        self.assertEqual(flow.resolve(cfg, 'implementation_model', 'test-standard', ['test-standard'])['model'], 'test-standard')
        cfg['roles']['review_model']['model'] = 'test-economy'
        result = flow.resolve(self.validate(cfg), 'review_model', 'test-standard', ['test-standard', 'test-economy'])
        self.assertEqual(result['model'], 'test-economy')
        self.assertEqual(result['reasoning_effort'], 'medium')

    def test_unavailable_fallback_and_failed_model(self):
        role = self.cfg['roles']['research_model']
        role.update(model='test-economy', fallback='stop')
        cfg = self.validate(self.cfg)
        with self.assertRaises(ValueError): flow.resolve(cfg, 'research_model', 'test-standard', ['test-standard'])
        with self.assertRaises(ValueError): flow.resolve(cfg, 'research_model', 'test-standard', ['test-standard', 'test-economy'], 'test-economy')
        role['fallback'] = 'parent'
        cfg = self.validate(self.cfg)
        self.assertEqual(flow.resolve(cfg, 'research_model', 'test-standard', ['test-standard'])['model'], 'test-standard')

    def test_forbidden_and_unauthorized(self):
        for bad in ('Astra', 'gpt-6-astra', 'provider/ASTRA-alias'):
            cfg = copy.deepcopy(self.cfg); cfg['allowed_models'][bad] = 'economy'
            with self.subTest(bad=bad), self.assertRaises(ValueError): self.validate(cfg)
        for tier in ('premium', 'unknown'):
            cfg = copy.deepcopy(self.cfg); cfg['allowed_models']['expensive'] = tier
            with self.assertRaises(ValueError): self.validate(cfg)
        self.cfg['roles']['design_model']['model'] = 'test-standard'
        self.assertEqual(self.validate(self.cfg)['roles']['design_model']['model'], 'test-standard')

    def test_main_switch_and_config_bounds(self):
        self.cfg['roles']['orchestrator_model']['model'] = 'test-economy'
        with self.assertRaises(ValueError): flow.resolve(self.validate(self.cfg), 'orchestrator_model', 'test-standard', ['test-standard', 'test-economy'])
        for value in (0, 3, True):
            cfg = copy.deepcopy(self.cfg); cfg['max_subagents'] = value
            with self.assertRaises(ValueError): self.validate(cfg)


if __name__ == '__main__': unittest.main()
