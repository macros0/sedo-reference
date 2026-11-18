"""Publish public operational status independently of GitHub Pages deployment."""
import base64
import io
import json
import os
import re
import subprocess
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

REPO = 'macros0/sedo-reference'
STATUS_BRANCH = 'automation-status'
SOURCE = 'alexey-sagitov/sedo-xsd-collector'


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def run_info(run):
    if not run:
        return dict(state='unknown')
    state = ('processing' if run.get('status') != 'completed' else
             'success' if run.get('conclusion') == 'success' else
             'unknown' if run.get('conclusion') == 'unknown' else
             'cancelled' if run.get('conclusion') in ('cancelled', 'skipped', 'neutral') else 'failure')
    return dict(state=state, run_id=run['id'], url=run.get('html_url'),
                run_attempt=run.get('run_attempt', 1), started_at=run.get('run_started_at') or run.get('created_at'),
                finished_at=run.get('updated_at') if state not in ('processing', 'unknown') else None)


def targets_check(target, check):
    return (check and str(target.get('update_run_id')) == str(check['id'])
            and str(target.get('update_run_attempt', 1)) == str(check.get('run_attempt', 1)))


def publication_dispatch_matches(page, target):
    title = (f"Publish reference for check {target.get('update_run_id')} "
             f"attempt {target.get('update_run_attempt', 1)} at {target.get('head_sha')}")
    return page.get('event') == 'workflow_dispatch' and page.get('display_title') == title


def publication_receipt_matches(page, target):
    receipt = page.get('publication_receipt')
    return (page.get('event') == 'workflow_dispatch' and isinstance(receipt, dict)
            and all(str(receipt.get(key)) == str(value) for key, value in (
                ('pages_run_id', page['id']), ('pages_run_attempt', page.get('run_attempt', 1)),
                ('update_run_id', target.get('update_run_id')),
                ('update_run_attempt', target.get('update_run_attempt', 1)),
                ('checked_sha', target.get('head_sha')), ('checked_catalog_sha256', target['catalog_sha256'])))
            and re.fullmatch('[0-9a-f]{40}', str(receipt.get('candidate_sha')))
            and re.fullmatch('[0-9a-f]{64}', str(receipt.get('catalog_sha256'))))


def build_status(source, checks, pages, reports, legacy_catalogs, now=None, configured_source="sourcecraft"):
    heartbeat_error = None
    if source:
        try:
            validate_heartbeat(source)
        except ValueError as error:
            heartbeat_error = str(error)
            source = None
    latest = (source or {}).get('latest') or {}
    sc = dict(state=latest.get('state') if latest.get('state') in ('success','failure','processing') else 'success' if latest.get('complete') else 'failure' if latest else 'unknown',
              started_at=latest.get('started_at'), finished_at=latest.get('finished_at'),
              contours=latest.get('contours', {}), run_id=latest.get('run_id'),
              url='https://sourcecraft.dev/'+SOURCE,
              last_success_at=((source or {}).get('last_success') or {}).get('finished_at'))
    if latest.get('error'):
        sc['error'] = str(latest['error'])[:500]
    if heartbeat_error:
        sc['error'] = heartbeat_error
    check = checks[0] if checks else None
    check_info = run_info(check)
    report = reports.get('update-report.json') or {}
    complete = check_info['state'] == 'success' and report.get('status') == 'ok'
    imported = (reports.get('sourcecraft-report.json') or {}).get('latest', {})
    collector_source = report.get('collector_source') or ('sourcecraft' if imported else configured_source)
    source_complete = latest.get('state') == 'success' or latest.get('complete') is True
    source_synced = collector_source == 'home' or (
        not heartbeat_error and (not source_complete or all(
            re.fullmatch(r'[0-9a-f]{64}', sc['contours'].get(env, {}).get('content_id') or '')
            and imported.get(env) == sc['contours'][env]['content_id']
            for env in ('prod', 'test'))))
    if not source_synced:
        complete = False
        if check_info['state'] == 'success':
            check_info['state'] = 'waiting'
    if collector_source != 'home' and not latest:
        complete = False
    revisions = report.get('new_schema_revisions', report.get('new_revisions'))
    versions = dict(count=len(revisions) if complete and isinstance(revisions, list) else None,
                    checked_at=report.get('checked_at') if complete else None)
    target = reports.get('publication-target.json') or {}
    publication = dict(state='not_published' if check_info['state'] in ('failure','cancelled') else 'waiting' if check else 'unknown')
    if check and report.get('status') == 'ok' and (source_synced or not latest and not heartbeat_error):
        expected = target.get('head_sha') if targets_check(target, check) else None
        started = check.get('run_started_at') or check.get('created_at', '')
        candidates = sorted((p for p in pages if (p.get('run_started_at') or p.get('created_at', '')) >= started),
                            key=lambda p: (p.get('run_started_at') or p.get('created_at', ''), p['id']), reverse=True)
        if 'catalog_sha256' in target:
            matched = next((p for p in candidates if expected and (
                publication_receipt_matches(p, target) or publication_dispatch_matches(p, target))), None)
        else:
            matched = next((p for p in candidates if
                (expected and p.get('head_sha') == expected) or
                (not target and report.get('checked_at') and
                 legacy_catalogs.get(p.get('head_sha')) == report['checked_at'])), None)
        if matched:
            publication = run_info(matched)
            if 'catalog_sha256' in target:
                if publication_receipt_matches(matched, target):
                    receipt = matched['publication_receipt']
                    publication['candidate_sha'] = receipt['candidate_sha']
                    if publication['state'] == 'success' and receipt['catalog_sha256'] != target['catalog_sha256']:
                        publication['state'] = 'superseded'
                elif publication['state'] == 'success':
                    publication['state'] = 'unknown'
    # A cancelled/failed check without a deployment target has no candidate to
    # track. A later independent Pages run still has its own actual outcome.
    has_target = targets_check(target, check) and target.get('head_sha')
    if check and check_info['state'] in ('failure', 'cancelled') and publication['state'] == 'not_published' and not has_target:
        started = check.get('run_started_at') or check.get('created_at', '')
        independent = max((page for page in pages if page.get('created_at', '') > started
                           and not (page.get('display_title') or '').startswith('Publish reference for check ')),
                          key=lambda page: (page['created_at'], page['id']), default=None)
        if independent:
            publication = run_info(independent)
    previous = latest_success(pages)
    return dict(format=1, generated_at=now or utcnow(), collector_source=collector_source, sourcecraft=sc,
                github=dict(check=check_info, versions=versions, publication=publication,
                            last_successful_publication=run_info(previous)))

def latest_success(runs):
    return max((run for run in runs if run_info(run)['state'] == 'success'),
               key=lambda run: (run['updated_at'], run['id']), default=None)


def publication_jobs(run_id, attempt):
    number = 1
    while True:
        jobs = api(f'actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100&page={number}')['jobs']
        yield from jobs
        if len(jobs) < 100:
            return
        number += 1


def publication_evidence(run, *, include_first_success=False):
    """Follow a status-only retry to the attempt that ran the deployment."""
    attempt = run.get('run_attempt', 1)
    first_success = attempt == 1 and run.get('conclusion') == 'success'
    if run.get('status') != 'completed' or (first_success and not include_first_success):
        return run
    for number in range(attempt, 0, -1):
        jobs = list(publication_jobs(run['id'], number))
        deploy = next((job for job in jobs if job['name'] == 'deploy'), None)
        if deploy and deploy.get('conclusion') == 'success':
            original = run if number == attempt else api(f"actions/runs/{run['id']}/attempts/{number}")
            return dict(original, run_attempt=number, status='completed', conclusion='success',
                        updated_at=deploy['completed_at'], deploy_time_verified=True)
        if not jobs or any(job['name'] != 'refresh-status' for job in jobs):
            break
    if not first_success and run.get('conclusion') == 'success':
        return dict(run, conclusion='unknown')
    return run


def api(path, payload=None, binary=False):
    command = ['gh', 'api', 'repos/'+REPO+'/'+path]
    if payload is not None:
        command += ['--method', 'POST', '--input', '-']
    result = subprocess.run(command, input=json.dumps(payload).encode() if payload is not None else None,
                            capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError('GitHub API '+path.split('?')[0]+': '+result.stderr.decode(errors='replace')[:350])
    return result.stdout if binary else json.loads(result.stdout)


def read_public(url, limit=8*1024*1024):
    with urlopen(Request(url, headers={'User-Agent':'SEDO-automation-status/1.0'}), timeout=30) as response:
        raw = response.read(limit+1)
    if len(raw) > limit:
        raise ValueError('Status input exceeds size limit')
    return raw


def sourcecraft_status():
    result = subprocess.run(['git', '-c', 'credential.helper=', 'ls-remote',
        'https://git@git.sourcecraft.dev/'+SOURCE+'.git', 'refs/heads/status'],
        check=True, capture_output=True, text=True, timeout=45)
    rows = result.stdout.split()
    if len(rows) != 2 or not re.fullmatch('[0-9a-f]{40}', rows[0]) or rows[1] != 'refs/heads/status':
        raise ValueError('SourceCraft heartbeat is unavailable')
    data = json.loads(read_public('https://raw.sourcecraft.tech/raw/'+SOURCE+'/'+rows[0]+'/status.json', 1024*1024))
    if not isinstance(data, dict) or data.get('format') != 1:
        raise ValueError('Invalid SourceCraft heartbeat format')
    validate_heartbeat(data)
    return data


def validate_heartbeat(data):
    if not isinstance(data, dict) or not isinstance(data.get('latest'), dict):
        raise ValueError('Invalid SourceCraft heartbeat')
    latest = data['latest']
    contours = latest.get('contours', {})
    if not isinstance(contours, dict) or set(contours) - {'prod', 'test'}:
        raise ValueError('Invalid SourceCraft heartbeat contours')
    def check_fields(record, fields):
        if not isinstance(record, dict):
            raise ValueError('Invalid SourceCraft heartbeat record')
        for name, types in fields.items():
            if record.get(name) is not None and type(record[name]) not in types:
                raise ValueError('Invalid SourceCraft heartbeat field: ' + name)
    check_fields(latest, dict(state=(str,), complete=(bool,), started_at=(str,), finished_at=(str,),
                              error=(str,), run_id=(str, int)))
    for record in contours.values():
        check_fields(record, dict(content_id=(str,), observed_at=(str,), changed=(bool,)))
    if latest.get('state') == 'success' or latest.get('complete') is True:
        timestamps = [latest.get('finished_at'), *(contours.get(env, {}).get('observed_at') for env in ('prod', 'test'))]
        for value in timestamps:
            try:
                parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    raise ValueError('Timestamp requires timezone')
            except (AttributeError, TypeError, ValueError) as error:
                raise ValueError('Successful SourceCraft heartbeat requires completion and contour observation timestamps') from error
    if data.get('last_success') is not None:
        check_fields(data['last_success'], dict(finished_at=(str,)))


def artifact_reports(run, names, filenames):
    number = 1
    while True:
        artifacts = api(f"actions/runs/{run['id']}/artifacts?per_page=100&page={number}")['artifacts']
        artifact = next((a for a in artifacts if a['name'] in names and not a['expired']), None)
        if artifact:
            break
        if len(artifacts) < 100:
            return {}
        number += 1
    # gh follows the authenticated API redirect safely; no token is sent to the signed storage URL.
    raw = api('actions/artifacts/'+str(artifact['id'])+'/zip', binary=True)
    if len(raw) > 8*1024*1024:
        raise ValueError('Status artifact exceeds size limit')
    reports = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for name in filenames:
            if name in archive.namelist():
                if archive.getinfo(name).file_size > 2*1024*1024:
                    raise ValueError('Status report exceeds size limit')
                reports[name] = json.loads(archive.read(name))
    return reports


def run_reports(run):
    if not run or run.get('status') != 'completed':
        return {}
    attempt = run.get('run_attempt', 1)
    names = {f'sfr-update-report-{attempt}'}
    if attempt == 1:
        names.add('sfr-update-report')
    return artifact_reports(run, names, ('update-report.json', 'sourcecraft-report.json', 'publication-target.json'))


def publication_receipts(pages, target, check, errors):
    if not targets_check(target, check):
        return pages
    started = check.get('run_started_at') or check.get('created_at', '')
    result = []
    for page in pages:
        if (page.get('event') == 'workflow_dispatch' and page.get('status') == 'completed'
                and (page.get('run_started_at') or page.get('created_at', '')) >= started):
            try:
                name = f"pages-publication-receipt-{page.get('run_attempt', 1)}"
                receipt = artifact_reports(page, {name}, ('publication-receipt.json',)).get('publication-receipt.json')
                if receipt:
                    page = dict(page, publication_receipt=receipt)
            except (ValueError, KeyError, RuntimeError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
                errors.append('Publication receipt: ' + str(error)[:350])
        result.append(page)
    return result


def active_check_runs():
    runs = []
    for state in ('in_progress', 'queued', 'waiting', 'pending', 'requested'):
        runs.extend(api(f'actions/workflows/check-sfr.yml/runs?branch=main&status={state}&per_page=100')['workflow_runs'])
    return runs


def run_freshness(run):
    return (run.get('run_attempt', 1), run.get('updated_at', ''), run.get('status') == 'completed')


def check_history(checks):
    by_id = {run['id']: run for run in checks}
    number = 1
    while True:
        batch = api(f'actions/workflows/check-sfr.yml/runs?per_page=100&page={number}')
        runs = batch['workflow_runs']
        for run in runs:
            if run.get('head_branch') == 'main':
                existing = by_id.get(run['id'])
                if not existing or run_freshness(run) > run_freshness(existing):
                    by_id[run['id']] = run
        if len(runs) < 100 or number * 100 >= batch.get('total_count', float('inf')):
            return list(by_id.values())
        number += 1


def successful_prior_attempts(pages, errors=None):
    for page in pages:
        for attempt in range(page.get('run_attempt', 1) - 1, 0, -1):
            try:
                candidate = api(f"actions/runs/{page['id']}/attempts/{attempt}")
                candidate = publication_evidence(candidate)
            except (RuntimeError, KeyError, OSError, subprocess.SubprocessError) as error:
                if errors is not None:
                    errors.append(f"Pages history run {page['id']} attempt {attempt}: {str(error)[:350]}")
                continue
            if run_info(candidate)['state'] == 'success':
                yield candidate


def publication_history(pages, incomplete):
    if not incomplete:
        return pages
    by_id = {page['id']: page for page in pages}
    number = 1
    while True:
        batch = api(f'actions/workflows/pages.yml/runs?per_page=100&page={number}')
        runs = batch['workflow_runs']
        for run in runs:
            if run.get('head_branch') == 'main':
                existing = by_id.get(run['id'])
                if not existing or run_freshness(run) > run_freshness(existing):
                    by_id[run['id']] = run
        if len(runs) < 100 or number * 100 >= batch.get('total_count', float('inf')):
            return list(by_id.values())
        number += 1


def event_attempt(run_id, attempt=None):
    current = api(f'actions/runs/{int(run_id)}')
    if attempt is not None and current.get('run_attempt', 1) < int(attempt):
        current = api(f'actions/runs/{int(run_id)}/attempts/{int(attempt)}')
        if current.get('run_attempt', 1) < int(attempt):
            raise RuntimeError('GitHub has not returned the requested workflow attempt')
    return current


def collect_status(publication_run_id=None, check_run_id=None, check_run_attempt=None, publication_run_attempt=None):
    finished_run = None
    if publication_run_id is not None:
        # The final Pages job dispatches us just before its own run becomes terminal.
        for attempt in range(24):
            current = event_attempt(publication_run_id, publication_run_attempt)
            if current.get('head_branch') != 'main' or current.get('path') != '.github/workflows/pages.yml':
                break
            if current['status'] == 'completed':
                finished_run = current
                break
            if attempt < 23:
                time.sleep(5)
        else:
            raise RuntimeError(f'Pages run {publication_run_id} did not finish in time')
    errors = []
    try:
        source = sourcecraft_status()
    except Exception as error:
        source = None
        errors.append('SourceCraft: '+str(error)[:350])
    checks = api('actions/workflows/check-sfr.yml/runs?branch=main&per_page=10')['workflow_runs']
    if check_run_id:
        event_run = event_attempt(check_run_id, check_run_attempt)
        if event_run.get('head_branch') == 'main' and event_run.get('path') == '.github/workflows/check-sfr.yml':
            checks = [event_run, *(run for run in checks if run['id'] != event_run['id'])]
    # Retries retain their creation date. Query active runs to find old runs
    # outside the first page, then compare the actual start of each attempt.
    active_checks = active_check_runs()
    by_check = {}
    for run in [*check_history(checks), *active_checks, *checks]:
        if run['id'] not in by_check or run_freshness(run) > run_freshness(by_check[run['id']]):
            by_check[run['id']] = run
    checks = sorted(by_check.values(), key=lambda run: (run.get('run_started_at') or run.get('created_at', ''), run['id']), reverse=True)
    page_result = api('actions/workflows/pages.yml/runs?branch=main&per_page=10')
    pages = page_result['workflow_runs']
    incomplete = page_result.get('total_count', len(pages)) > len(pages)
    if finished_run:
        # Each rerun shares its run ID with older attempts. A listing can lag
        # the direct lookup, omit the run, or already include a newer attempt.
        by_id = {page['id']: page for page in pages}
        listed = by_id.get(finished_run['id'])
        if listed is None or run_freshness(listed) <= run_freshness(finished_run):
            by_id[finished_run['id']] = finished_run
        pages = sorted(by_id.values(), key=lambda page: (page['created_at'], page['id']), reverse=True)
    pages = publication_history(pages, incomplete)
    first_successes = {}

    def first_deployment(run):
        if run.get('deploy_time_verified'):
            return run
        if run['id'] not in first_successes:
            try:
                first_successes[run['id']] = publication_evidence(run, include_first_success=True)
            except (RuntimeError, KeyError, OSError, subprocess.SubprocessError):
                # A first successful workflow proves deployment even if the
                # service cannot supply its exact job completion timestamp.
                first_successes[run['id']] = run
        evidence = first_successes[run['id']]
        return dict(run, updated_at=evidence['updated_at'], deploy_time_verified=evidence.get('deploy_time_verified', False))

    def latest_deployment(runs, best=None):
        successful = sorted((run for run in runs if run_info(run)['state'] == 'success'),
                            key=lambda run: (run['updated_at'], run['id']), reverse=True)
        for run in successful:
            # A deployment finishes no later than its workflow.
            if best and run['updated_at'] < best['updated_at']:
                break
            evidence = first_deployment(run) if run.get('run_attempt', 1) == 1 else run
            best = latest_success([best, evidence] if best else [evidence])
        return best

    known_success = latest_deployment(page for page in pages if page.get('run_attempt', 1) == 1)
    current_target = None
    def deployment(run):
        if (known_success and run.get('updated_at', '') < known_success['updated_at']
                and (run['id'], run.get('run_attempt', 1)) != current_target):
            return run
        try:
            if ('catalog_sha256' in target and (run['id'], 1) == current_target
                    and run_info(run)['state'] == 'success'):
                return first_deployment(run)
            return publication_evidence(run)
        except (RuntimeError, KeyError, OSError, subprocess.SubprocessError) as error:
            errors.append('Deployment evidence: ' + str(error)[:350])
            if run.get('run_attempt', 1) > 1 and run.get('conclusion') == 'success':
                return dict(run, conclusion='unknown')
            return run
    try:
        reports = run_reports(checks[0] if checks else None)
    except Exception as error:
        reports = {}
        errors.append('Reports: '+str(error)[:350])
    legacy = {}
    target = reports.get('publication-target.json') or {}
    check_started = (checks[0].get('run_started_at') or checks[0].get('created_at', '')) if checks else ''
    if 'catalog_sha256' in target:
        pages = publication_receipts(pages, target, checks[0] if checks else None, errors)
        eligible_targets = [page for page in pages if publication_receipt_matches(page, target)
                            or publication_dispatch_matches(page, target)]
    else:
        eligible_targets = [page for page in pages if target.get('head_sha')
                            and page.get('head_sha') == target['head_sha']
                            and (page.get('run_started_at') or page.get('created_at', '')) >= check_started]
    target_page = max(eligible_targets, key=lambda page: (page.get('run_started_at') or page.get('created_at', ''), page['id']), default=None)
    if target_page:
        current_target = (target_page['id'], target_page.get('run_attempt', 1))
    deployments = []
    for page in pages:
        actual = deployment(page)
        if 'catalog_sha256' in target and actual.get('run_attempt', 1) != page.get('run_attempt', 1):
            actual, = publication_receipts([actual], target, checks[0] if checks else None, errors)
        deployments.append(actual)
    pages = deployments
    expected = target.get('head_sha')
    if ('catalog_sha256' not in target and checks and str(target.get('update_run_id')) == str(checks[0]['id'])
            and isinstance(expected, str) and re.fullmatch('[0-9a-f]{40}', expected)
            and not any(p.get('head_sha') == expected and (p.get('run_started_at') or p.get('created_at', '')) >=
                        (checks[0].get('run_started_at') or checks[0].get('created_at', '')) for p in pages)):
        matches = api('actions/workflows/pages.yml/runs?branch=main&head_sha='+expected+'&per_page=100')['workflow_runs']
        by_id = {p['id']: p for p in [*(deployment(page) for page in matches), *pages]}
        pages = sorted(by_id.values(), key=lambda p: (p['created_at'], p['id']), reverse=True)
    update = reports.get('update-report.json') or {}
    if update.get('status') == 'ok' and not reports.get('publication-target.json'):
        started = (checks[0].get('run_started_at') or checks[0].get('created_at', '')) if checks else ''
        candidates = sorted((page for page in pages if (page.get('run_started_at') or page.get('created_at', '')) >= started),
                            key=lambda page: (page.get('run_started_at') or page.get('created_at', ''), page['id']), reverse=True)
        for page in candidates:
            digest = page.get('head_sha', '')
            if not re.fullmatch('[0-9a-f]{40}', digest) or digest in legacy:
                continue
            try:
                catalog = json.loads(read_public('https://raw.githubusercontent.com/'+REPO+'/'+digest+'/data/catalog.json'))
                legacy[digest] = catalog.get('checked_at')
            except Exception:
                legacy[digest] = None
            if legacy[digest] == update.get('checked_at'):
                break
    result = build_status(source, checks, pages, reports, legacy,
                          configured_source=os.environ.get('SEDO_COLLECTOR_SOURCE') or 'sourcecraft')
    last = latest_deployment(pages)
    if incomplete or result['github']['last_successful_publication']['state'] == 'unknown':
        # Run creation order does not reflect when a deployment was retried.
        # Read successes independently of the currently targeted SHA.
        number = 1
        filters = 'branch=main&status=success&'
        while True:
            batch = api(f'actions/workflows/pages.yml/runs?{filters}per_page=100&page={number}')
            if filters and batch.get('total_count', 0) > 1000:
                # Filtered Actions searches expose at most 1000 results.
                # The workflow's unfiltered run history supports full pagination.
                filters = ''
                continue
            runs = batch['workflow_runs']
            candidates = (deployment(run) for run in runs if run.get('head_branch') == 'main')
            last = latest_deployment(candidates, last)
            if len(runs) < 100 or number * 100 >= batch.get('total_count', float('inf')):
                break
            number += 1
    # Run listings expose only the latest attempt. A failed retry leaves the
    # earlier successful deployment live, so inspect prior attempts as well.
    last_time = last['updated_at'] if last else ''
    relevant_history = [page for page in pages if page.get('updated_at', '') >= last_time]
    result['github']['last_successful_publication'] = run_info(
        latest_deployment(successful_prior_attempts(relevant_history, errors), last))
    result['errors'] = errors
    return result


def publish_status(status):
    try:
        head = api('git/ref/heads/'+STATUS_BRANCH)['object']['sha']
    except RuntimeError as error:
        if '404' not in str(error):
            raise
        head = None
    raw = (json.dumps(status, ensure_ascii=False, indent=2)+'\n').encode()
    blob = api('git/blobs', dict(content=base64.b64encode(raw).decode(), encoding='base64'))['sha']
    tree = api('git/trees', dict(tree=[dict(path='status.json', mode='100644', type='blob', sha=blob)]))['sha']
    commit = api('git/commits', dict(message='Refresh automatic update status', tree=tree, parents=[head] if head else [],
        author=dict(name='github-actions[bot]', email='41898282+github-actions[bot]@users.noreply.github.com')))['sha']
    if head:
        # POST is not supported by refs update; keep the method explicit and never force history.
        payload = json.dumps(dict(sha=commit, force=False)).encode()
        subprocess.run(['gh','api','repos/'+REPO+'/git/refs/heads/'+STATUS_BRANCH,
                        '--method','PATCH','--input','-'], input=payload, check=True,
                       capture_output=True, timeout=60)
    else:
        api('git/refs', dict(ref='refs/heads/'+STATUS_BRANCH, sha=commit))


if __name__ == '__main__':
    status = collect_status(os.environ.get('PUBLICATION_RUN_ID') or None, os.environ.get('CHECK_RUN_ID') or None,
                            os.environ.get('CHECK_RUN_ATTEMPT') or None, os.environ.get('PUBLICATION_RUN_ATTEMPT') or None)
    Path('automation-status-report.json').write_text(json.dumps(status, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    publish_status(status)
    print(json.dumps(dict(sourcecraft=status['sourcecraft']['state'],
        check=status['github']['check']['state'], publication=status['github']['publication']['state']), ensure_ascii=False))
