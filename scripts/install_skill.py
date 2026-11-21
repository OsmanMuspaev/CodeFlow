#!/usr/bin/env python3
"""Install the self-contained workflow skill into a project, never global settings."""
import argparse
from pathlib import Path
import shutil
import sys


def install(project):
    project = Path(project).resolve(strict=True)
    if not project.is_dir():
        raise ValueError('Project must be a directory')
    destination = project / '.agents/skills/workflow'
    for path in (project / '.agents', project / '.agents/skills', destination):
        if path.is_symlink():
            raise ValueError('Refusing symlink: ' + str(path))
    if destination.exists():
        raise ValueError('Skill already exists; inspect it before manually updating')
    source = Path(__file__).resolve().parents[1] / 'plugins/codeflow/skills/workflow'
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    args = parser.parse_args()
    try:
        print(install(args.project))
    except (OSError, ValueError) as exc:
        sys.exit('codeflow: ' + str(exc))
