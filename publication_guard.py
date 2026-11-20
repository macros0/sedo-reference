"""Reject a Pages artifact whose validated commit is no longer main."""
import argparse
import json
import os
import re
import subprocess
from urllib.request import Request, urlopen

IMPORT_SUBJECT = b'Import specifications and validated collector snapshots'


def resume_build_candidate(base, current, run_id, *, cwd=None):
    """Identify the validated input commit left by an earlier attempt of this run."""
    if not all(re.fullmatch(r'[0-9a-f]{40}', sha) for sha in (base, current)) or not re.fullmatch(r'[0-9]+', run_id):
        raise ValueError('Missing or invalid Pages run identity')
    if current == base:
        return None
    raw = subprocess.check_output(['git', 'cat-file', 'commit', current], cwd=cwd)
    headers, _, message = raw.partition(b'\n\n')
    parents = [line for line in headers.splitlines() if line.startswith(b'parent ')]
    expected = IMPORT_SUBJECT + b'\n\nSEDO-Pages-Run: ' + run_id.encode('ascii')
    if parents != [b'parent ' + base.encode('ascii')] or message.rstrip(b'\n') != expected:
        return None
    changes = subprocess.check_output(['git', 'diff-tree', '-r', '--no-commit-id', '--name-only', '-z', base, current], cwd=cwd)
    paths = changes.split(b'\0')[:-1]
    return current if paths and all(path.startswith((b'state/', b'data/')) for path in paths) else None


def select_build_candidate(base, candidate, current, *, cwd=None):
    """Resume a tested tree already saved as a sibling of the retry commit."""
    if not all(re.fullmatch(r'[0-9a-f]{40}', sha) for sha in (base, candidate, current)):
        raise ValueError('Missing or invalid Pages commit SHA')
    if current in (base, candidate):
        return candidate

    def metadata(sha):
        # A depth-one Actions checkout still has the raw parent headers.
        raw = subprocess.check_output(['git', 'cat-file', 'commit', sha], cwd=cwd)
        headers = raw.split(b'\n\n', 1)[0].splitlines()
        return headers[0], [line for line in headers if line.startswith(b'parent ')]

    if candidate != base:
        tree, parents = metadata(candidate)
        if parents == [b'parent ' + base.encode('ascii')] and metadata(current) == (tree, parents):
            return current
    raise ValueError(f'Stale Pages candidate {candidate}: main is {current}; rebuild current main')


def ensure_current(candidate):
    if not re.fullmatch(r'[0-9a-f]{40}', candidate):
        raise ValueError('Missing or invalid built candidate SHA')
    repository = os.environ['GITHUB_REPOSITORY']
    request = Request('https://api.github.com/repos/' + repository + '/git/ref/heads/main', headers={
        'Accept': 'application/vnd.github+json',
        'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
        'User-Agent': 'SEDO-reference-publication-guard',
        # A rerun must check main now, not a cached observation from its build.
        'Cache-Control': 'no-cache',
    })
    with urlopen(request, timeout=30) as response:
        current = json.load(response)['object']['sha']
    if candidate != current:
        raise ValueError(f'Stale Pages candidate {candidate}: main is {current}; rebuild before deployment')
    return candidate


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    args = parser.add_mutually_exclusive_group(required=True)
    args.add_argument('candidate_sha', nargs='?')
    args.add_argument('--build-candidate', nargs=3, metavar=('BASE', 'CANDIDATE', 'CURRENT'))
    args.add_argument('--resume-build', nargs=3, metavar=('BASE', 'CURRENT', 'RUN_ID'))
    options = parser.parse_args()
    if options.build_candidate:
        print(select_build_candidate(*options.build_candidate))
    elif options.resume_build:
        print(resume_build_candidate(*options.resume_build) or '')
    else:
        print('Validated Pages candidate ' + ensure_current(options.candidate_sha))
