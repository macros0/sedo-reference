"""Reject a Pages artifact whose validated commit is no longer main."""
import argparse
import json
import os
import re
from urllib.request import Request, urlopen


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
    parser.add_argument('candidate_sha')
    print('Validated Pages candidate ' + ensure_current(parser.parse_args().candidate_sha))
