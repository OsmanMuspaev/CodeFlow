#!/usr/bin/env python3
"""Prepare a self-contained local marketplace at a new destination."""
import argparse
from pathlib import Path
import shutil
import sys


def prepare(destination):
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError('Destination already exists; choose a new directory')
    root = Path(__file__).resolve().parents[1]
    destination.mkdir(parents=True)
    shutil.copytree(root / 'plugins/codeflow', destination / 'plugins/codeflow', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    catalog = destination / '.agents/plugins/marketplace.json'
    catalog.parent.mkdir(parents=True)
    shutil.copyfile(root / '.agents/plugins/marketplace.json', catalog)
    shutil.copyfile(root / 'LICENSE', destination / 'LICENSE')
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    try:
        print(prepare(args.destination))
    except (OSError, ValueError) as exc:
        sys.exit('codeflow: ' + str(exc))
