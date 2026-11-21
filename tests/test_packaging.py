import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class PackagingTests(unittest.TestCase):
    def test_standalone_installs_full_skill_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            installer = module('install_skill')
            target = installer.install(tmp)
            self.assertTrue((target / 'scripts/flow.py').is_file())
            self.assertTrue((target / 'config/default.json').is_file())
            (target / 'user.txt').write_text('preserve')
            with self.assertRaises(ValueError): installer.install(tmp)
            self.assertEqual((target / 'user.txt').read_text(), 'preserve')
            result = subprocess.run([sys.executable, str(target / 'scripts/flow.py'), '--root', tmp, 'config'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['allowed_models'], {'gpt-6.2-sol': 'standard', 'gpt-6-luna': 'economy'})

    def test_begin_validates_model_before_creating_run(self):
        script = ROOT / 'plugins/codexflow/skills/workflow/scripts/flow.py'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = dict(os.environ, CODEX_HOME=str(root / 'codex-home'))
            task = root / 'task.txt'
            task.write_text('Fix the local function')
            cmd = [sys.executable, str(script), '--root', tmp, '--compact',
                   '--set', 'model_selection="pinned"', 'begin', '--task-file', str(task)]
            rejected = subprocess.run([*cmd, '--parent', 'gpt-6.2-astra'], env=env, capture_output=True)
            self.assertEqual(rejected.returncode, 2)
            self.assertFalse((root / '.codexflow/runs').exists())
            accepted = subprocess.run([*cmd, '--parent', 'gpt-6.2-sol'], env=env,
                                      capture_output=True, text=True, check=True)
            run = json.loads(accepted.stdout)
            self.assertEqual(run['next_stage'], 'CLASSIFICATION')
            self.assertEqual(run['main_models']['orchestrator_model']['actual_model'], 'gpt-6.2-sol')
            saved = root / '.codexflow/runs' / run['run_id']
            self.assertEqual(json.loads((saved / 'model-catalog.json').read_text()), {})
            self.assertEqual(json.loads((saved / 'state.json').read_text())['policy']['model_selection'], 'pinned')

    def test_config_init_reads_effective_defaults_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = ROOT / 'plugins/codexflow/skills/workflow/scripts/flow.py'
            args = [sys.executable, str(script), '--root', tmp, 'config', '--init']
            result = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            config_file = Path(tmp) / '.codexflow/config.json'
            data = json.loads(result.stdout)
            self.assertEqual(data['roles']['implementation_model']['model'], 'gpt-6.2-sol')
            self.assertEqual(data['roles']['research_model']['model'], 'gpt-6-luna')
            data['report_words'] = 321
            config_file.write_text(json.dumps(data))
            before = config_file.read_bytes()
            result = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(config_file.read_bytes(), before)

    def test_profile_layers_and_run_policy_survive_config_changes(self):
        script = ROOT / 'plugins/codexflow/skills/workflow/scripts/flow.py'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = dict(os.environ, CODEX_HOME=str(root / 'codex-home'))

            def call(*args):
                result = subprocess.run(
                    [sys.executable, str(script), '--root', tmp, *args],
                    text=True, capture_output=True, env=env,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)

            call('--profile', 'speed', '--set', 'report_words=300', 'config', '--global', '--save')
            self.assertEqual(call('config')['profile'], 'speed')
            call('--profile', 'performance', '--set', 'roles.review_model.reasoning_effort="medium"',
                 '--set', 'default_mode="full"', 'config', '--save')
            merged = call('config')
            self.assertEqual(merged['profile'], 'performance')
            self.assertEqual(merged['report_words'], 300)
            self.assertEqual(merged['roles']['review_model']['model'], 'gpt-6.2-sol')
            self.assertEqual(merged['roles']['review_model']['reasoning_effort'], 'medium')
            self.assertEqual(call('config', '--global')['profile'], 'speed')
            task = root / 'task.txt'
            task.write_text('Add focused tests')
            run = call('--profile', 'efficiency', '--set', 'max_subagents=1',
                       'start', '--task-file', str(task))
            self.assertEqual(run['mode'], 'full')
            self.assertEqual(run['policy']['profile'], 'efficiency')
            self.assertEqual(run['policy']['max_subagents'], 1)
            self.assertFalse(run['policy']['allow_premium'])
            call('--set', 'report_words=800', 'config', '--save')
            call('resume', run['run_id'])
            saved = call('--run-id', run['run_id'], 'config')
            self.assertEqual(saved, run['policy'])
            self.assertEqual(call('config')['report_words'], 800)
            resolved = call('--run-id', run['run_id'], 'resolve', 'orchestrator_model')
            self.assertEqual(resolved['requested_model'], 'gpt-6.2-sol')

    def test_marketplace_is_self_contained_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            maker = module('prepare_marketplace')
            market = maker.prepare(Path(tmp) / 'market')
            catalog = json.loads((market / '.agents/plugins/marketplace.json').read_text())
            path = market / catalog['plugins'][0]['source']['path']
            self.assertTrue((path / '.codex-plugin/plugin.json').is_file())
            self.assertTrue((path / 'skills/workflow/references/models.md').is_file())
            with self.assertRaises(ValueError): maker.prepare(market)

    def test_compact_start_preserves_checkpoint_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = ROOT / 'plugins/codexflow/skills/workflow/scripts/flow.py'
            task = Path(tmp) / 'task.txt'; task.write_text('Fix a local bug')
            result = subprocess.run([sys.executable, str(script), '--root', tmp, '--compact', 'start', '--task-file', str(task), '--mode', 'quick'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual(summary['revision'], 0)
            self.assertEqual(summary['next_stage'], 'CLASSIFICATION')
            state = json.loads((Path(tmp) / '.codexflow/runs' / summary['run_id'] / 'state.json').read_text())
            self.assertEqual(state['run_id'], summary['run_id'])
            self.assertIn('stages', state)
            self.assertNotIn('stages', summary)

    def test_resolve_cli_continues_with_honest_unconfirmed_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = ROOT / 'plugins/codexflow/skills/workflow/scripts/flow.py'
            result = subprocess.run([sys.executable, str(script), '--root', tmp, 'resolve', 'orchestrator_model'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            selected = json.loads(result.stdout)
            self.assertEqual(selected['requested_model'], 'gpt-6.2-sol')
            self.assertIsNone(selected['actual_model'])
            self.assertEqual(selected['runtime_verification'], 'unconfirmed')

    def test_package_and_cli_error_messages(self):
        self.assertEqual(module('validate_package').validate(ROOT), 'CodexFlow package valid')
        with tempfile.TemporaryDirectory() as tmp:
            script = ROOT / 'plugins/codexflow/skills/workflow/scripts/flow.py'
            result = subprocess.run([sys.executable, str(script), '--root', tmp, 'status', '../outside'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('Invalid run ID', result.stderr)
            self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__': unittest.main()
