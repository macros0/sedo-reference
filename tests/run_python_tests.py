"""Run the complete unittest discovery suite in independent processes."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_MODULES = {'tests.test_auto_update', 'tests.test_inbox', 'tests.test_manual_import'}


def test_ids(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from test_ids(test)
        else:
            yield test.id()


def is_long_integration(name):
    return name.rsplit('.', 2)[0] in INTEGRATION_MODULES


def groups_for(names):
    integration, modules = [], {}
    for name in names:
        module = name.rsplit('.', 2)[0]
        if is_long_integration(name):
            integration.append((name, [name]))
        else:
            modules.setdefault(module, []).append(name)
    return integration + list(modules.items())


def run_group(group):
    label, names = group
    result = subprocess.run([sys.executable, '-B', '-m', 'unittest', '-q', *names], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace')
    lines = result.stdout.splitlines()
    summary = next((line for line in lines if line.startswith('Ran ')), '')
    count = f'Ran {len(names)} test{"s" if len(names) != 1 else ""} in '
    if result.returncode or not summary.startswith(count):
        print(f'{label}: failed ({result.returncode})\n{result.stdout}', flush=True)
        return 1
    outcome = next((line for line in lines if line == 'OK' or line.startswith('OK (')), '')
    print(f'{label}: {summary} {outcome}', flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, default=2)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    loader = unittest.TestLoader()
    # Python 3.12 cannot discover this namespace package with an explicit
    # top-level directory. Subprocesses can still import the qualified names.
    names = ['tests.' + name for name in test_ids(loader.discover(str(ROOT / 'tests')))]
    if loader.errors:
        raise SystemExit('\n'.join(loader.errors))
    if not names or len(names) != len(set(names)):
        raise SystemExit('Discovery returned no tests or duplicate test IDs')
    groups = groups_for(names)
    integration = sum(map(is_long_integration, names))
    print(f'Discovered {len(names)} Python tests, including {integration} long integration tests.', flush=True)
    with ThreadPoolExecutor(max_workers=args.jobs) as workers:
        failures = sum(bool(code) for code in workers.map(run_group, groups))
    print(f'Completed {len(groups)} groups. Failed groups: {failures}.', flush=True)
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
