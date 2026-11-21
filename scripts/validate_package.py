#!/usr/bin/env python3
"""Validate the supported CodeFlow compatibility package without dependencies."""
import json
from pathlib import Path
import re


def validate_docs(root):
    docs = [*root.glob('*.md'), *root.joinpath('docs').rglob('*.md'),
            *root.joinpath('plugins').rglob('*.md'), *root.joinpath('.github').rglob('*.md')]
    for path in docs:
        source = path.read_text(encoding='utf-8')
        if '.github' not in path.relative_to(root).parts:
            peer = (path.with_name(path.name.removesuffix('.ru.md') + '.md')
                    if path.name.endswith('.ru.md') else path.with_suffix('.ru.md'))
            assert peer.is_file(), f'Missing translation for {path}'
            assert f']({peer.name})' in source, f'Missing language switch in {path}'
        for target in re.findall(r'\]\(([^)]+)\)', source):
            if target.startswith(('https://', 'http://', 'mailto:', '#')):
                continue
            resolved = (path.parent / target.split('#', 1)[0]).resolve()
            assert resolved.is_relative_to(root) and resolved.exists(), f'Broken link in {path}: {target}'


def validate(root):
    root = Path(root).resolve()
    validate_docs(root)
    plugin = root / 'plugins/codeflow'
    manifest = json.loads((plugin / '.codex-plugin/plugin.json').read_text())
    assert manifest['name'] == 'codeflow'
    assert re.fullmatch(r'\d+\.\d+\.\d+', manifest['version'])
    assert manifest['skills'] == './skills/'
    market = json.loads((root / '.agents/plugins/marketplace.json').read_text())
    entry = next(item for item in market['plugins'] if item['name'] == manifest['name'])
    assert (root / entry['source']['path']).resolve() == plugin
    assert (root / 'LICENSE').is_file()
    skill = plugin / 'skills/workflow'
    entry = (skill / 'SKILL.md').read_text()
    assert entry.startswith('---\nname: workflow\ndescription: ')
    for target in re.findall(r'\]\(([^)]+)\)', entry):
        path = (skill / target).resolve()
        assert path.is_relative_to(skill.resolve()) and path.is_file(), target
    import importlib.util
    spec = importlib.util.spec_from_file_location('flow_validation', skill / 'scripts/flow.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Defaults are validated with no project override.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        cfg = module.config(Path(tmp))
        assert cfg['allowed_models']
        for role in module.ROLES:
            assert cfg['roles'][role]['model'] in cfg['allowed_models']
    for path in plugin.rglob('*'):
        assert not path.is_symlink(), path
    return 'CodeFlow package valid'


if __name__ == '__main__':
    print(validate(Path(__file__).resolve().parents[1]))
