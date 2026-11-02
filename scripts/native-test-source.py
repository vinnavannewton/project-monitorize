#!/usr/bin/env python3
"""Capture/validate an explicit development tree without changing the user index.

Native builders accept MONITORIZE_TEST_SOURCE_TREE only after validation. New
untracked source files require explicit --include arguments; ignored files and
directories cannot be swept into a package accidentally.
"""
import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile


def git(root, *arguments, data=None, env=None):
    result = subprocess.run(['git', '-C', str(root), *arguments], input=data, capture_output=True,
                            env=env, check=False)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors='replace').strip() or 'Git source inspection failed.')
    return result.stdout


def entries(root, tree):
    result = {}
    for record in git(root, 'ls-tree', '-rz', tree).split(b'\0'):
        if not record:
            continue
        metadata, name = record.split(b'\t', 1)
        mode, kind, oid = metadata.decode().split()
        result[os.fsdecode(name)] = {'mode': mode, 'kind': kind, 'oid': oid}
    return result


def validate(root, tree):
    if len(tree) not in (40, 64) or any(char not in '0123456789abcdef' for char in tree):
        raise ValueError('Use the full hexadecimal tree identity from capture.')
    if git(root, 'cat-file', '-t', tree).strip() != b'tree':
        raise ValueError('The test source identity must name a Git tree.')
    values = entries(root, tree)
    for name, entry in values.items():
        path = root/name
        if entry['mode'] == '160000':
            if git(path, 'rev-parse', 'HEAD').decode().strip() != entry['oid']:
                raise ValueError('Submodule changed since capture: ' + name)
            continue
        try:
            info = path.lstat()
        except FileNotFoundError as exc:
            raise ValueError('Source disappeared since capture: ' + name) from exc
        if entry['mode'] == '120000' and stat.S_ISLNK(info.st_mode):
            content = os.fsencode(os.readlink(path))
            oid = git(root, 'hash-object', '--stdin', data=content).decode().strip()
        elif entry['mode'] in ('100644', '100755') and stat.S_ISREG(info.st_mode):
            mode = '100755' if info.st_mode & 0o111 else '100644'
            if mode != entry['mode']:
                raise ValueError('Source permissions changed since capture: ' + name)
            oid = git(root, 'hash-object', '--path=' + name, '--stdin', data=path.read_bytes()).decode().strip()
        else:
            raise ValueError('Unsupported or changed source file: ' + name)
        if oid != entry['oid']:
            raise ValueError('Source changed since capture: ' + name)
    tracked = git(root, 'ls-files', '-z').split(b'\0')
    for name in tracked:
        if not name:
            continue
        name = os.fsdecode(name)
        path = root/name
        if (path.exists() or path.is_symlink()) and name not in values:
            raise ValueError('Tracked source is missing from the captured tree: ' + name)
    return values


def capture(root, includes):
    selected = []
    for name in includes:
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or not relative.parts:
            raise ValueError('Include a repository-relative source filename.')
        path = root/relative
        if not path.is_file() and not path.is_symlink():
            raise ValueError('Include individual files, not directories: ' + name)
        # Do not force ignored VM disks, credentials, caches or local settings.
        ignored = subprocess.run(['git', '-C', str(root), 'check-ignore', '-q', '--', str(path.absolute())])
        if ignored.returncode == 0:
            raise ValueError('Ignored files cannot enter a test package: ' + name)
        if ignored.returncode != 1:
            raise ValueError('Could not inspect source ignore rules.')
        selected.append(os.fsencode(name))
    base = git(root, 'rev-parse', 'HEAD').decode().strip()
    tracked = [name for name in git(root, 'ls-files', '-z').split(b'\0') if name]
    with tempfile.TemporaryDirectory(prefix='monitorize-source-index-') as temporary:
        environment = {**os.environ, 'GIT_INDEX_FILE': str(Path(temporary)/'index'), 'GIT_LITERAL_PATHSPECS': '1'}
        git(root, 'read-tree', 'HEAD', env=environment)
        paths = sorted(set(tracked + selected))
        if paths:
            git(root, 'add', '--all', '--pathspec-from-file=-', '--pathspec-file-nul',
                data=b'\0'.join(paths)+b'\0', env=environment)
        tree = git(root, 'write-tree', env=environment).decode().strip()
    values = validate(root, tree)
    return {'base_commit': base, 'tree': tree, 'development_only': True, 'files': values}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('capture')
    create.add_argument('--include', action='append', default=[])
    check = commands.add_parser('validate')
    check.add_argument('tree')
    args = parser.parse_args()
    try:
        root = Path(os.fsdecode(git(args.repository, 'rev-parse', '--show-toplevel')).strip())
        if args.command == 'capture':
            print(json.dumps(capture(root, args.include), indent=2, sort_keys=True))
        else:
            validate(root, args.tree)
            print(args.tree)
    except (OSError, ValueError) as exc:
        parser.exit(1, str(exc)+'\n')


if __name__ == '__main__':
    main()
