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
INTERRUPTED_CHECK_STATES = ('failure', 'cancelled')


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


def run_start(run):
    return run.get('run_started_at') or run.get('created_at', '')


def latest_independent_page(pages, linked_run_id):
    standalone = (page for page in pages if page.get('event') in ('push', 'workflow_dispatch')
                  and page['id'] != linked_run_id
                  and not page.get('publication_receipt')
                  and not (page.get('display_title') or '').startswith('Publish reference for check '))
    return max(standalone, key=lambda page: (run_start(page), page['id']), default=None)


def successful_capture(record):
    return record.get('state') == 'success' or record.get('complete') is True


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
    confirmed = latest if successful_capture(latest) else (source or {}).get('last_success') or {}
    contours = confirmed.get('contours', {})
    if collector_source == 'home':
        source_synced = True
    elif heartbeat_error:
        source_synced = False
    elif all(re.fullmatch(r'[0-9a-f]{64}', contours.get(env, {}).get('content_id') or '') for env in ('prod', 'test')):
        source_synced = all(imported.get(env) == contours[env]['content_id'] for env in ('prod', 'test'))
    else:
        source_synced = False if successful_capture(latest) else None
    if not source_synced:
        complete = False
        if check_info['state'] == 'success' and (latest or source_synced is False):
            check_info['state'] = 'waiting' if source_synced is False else 'unknown'
    revisions = report.get('new_schema_revisions', report.get('new_revisions'))
    versions = dict(count=len(revisions) if complete and isinstance(revisions, list) else None,
                    checked_at=report.get('checked_at') if complete else None)
    target = reports.get('publication-target.json') or {}
    started = run_start(check) if check else ''
    interrupted = check_info['state'] in INTERRUPTED_CHECK_STATES
    publication = dict(state='not_published' if interrupted else 'waiting' if check else 'unknown')
    if check and report.get('status') == 'ok':
        expected = target.get('head_sha') if targets_check(target, check) else None
        candidates = sorted((p for p in pages if run_start(p) >= started),
                            key=lambda p: (run_start(p), p['id']), reverse=True)
        matched = None
        if 'catalog_sha256' in target:
            matched = next((p for p in candidates if expected and (
                publication_receipt_matches(p, target) or publication_dispatch_matches(p, target))), None)
        elif not interrupted and (source_synced or not latest and not heartbeat_error):
            # A standalone push can share the SHA and catalog time of an interrupted check.
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
    independent = latest_independent_page(pages, publication.get('run_id'))
    previous = latest_success(pages)
    return dict(format=1, generated_at=now or utcnow(), collector_source=collector_source, sourcecraft=sc,
                github=dict(check=check_info, versions=versions, publication=publication,
                            independent_publication=run_info(independent),
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
    def check_fields(record, fields):
        if not isinstance(record, dict):
            raise ValueError('Invalid SourceCraft heartbeat record')
        for name, types in fields.items():
            if record.get(name) is not None and type(record[name]) not in types:
                raise ValueError('Invalid SourceCraft heartbeat field: ' + name)
    def check_capture(record, *, historical=False):
        check_fields(record, dict(state=(str,), complete=(bool,), started_at=(str,), finished_at=(str,),
                                  error=(str,), run_id=(str, int)))
        contours = record.get('contours', {})
        if not isinstance(contours, dict) or set(contours) - {'prod', 'test'}:
            raise ValueError('Invalid SourceCraft heartbeat contours')
        for contour in contours.values():
            check_fields(contour, dict(content_id=(str,), observed_at=(str,), changed=(bool,)))
        # Older heartbeats retained only the date of their last success.
        require_dates = 'contours' in record if historical else successful_capture(record)
        if require_dates:
            timestamps = [record.get('finished_at'), *(contours.get(env, {}).get('observed_at') for env in ('prod', 'test'))]
            for value in timestamps:
                try:
                    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                    if parsed.tzinfo is None:
                        raise ValueError('Timestamp requires timezone')
                except (AttributeError, TypeError, ValueError) as error:
                    raise ValueError('Successful SourceCraft heartbeat requires completion and contour observation timestamps') from error

    check_capture(data['latest'])
    if data.get('last_success') is not None:
        check_capture(data['last_success'], historical=True)


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
    latest_page = None
    errors = []
    if publication_run_id is not None:
        run_id = int(publication_run_id)
        requested = int(publication_run_attempt) if publication_run_attempt is not None else None
        # The final Pages job dispatches us just before its own run becomes terminal.
        for attempt in range(24):
            latest = api(f'actions/runs/{run_id}')
            if latest.get('head_branch') != 'main' or latest.get('path') != '.github/workflows/pages.yml':
                break
            if latest_page is None or run_freshness(latest) >= run_freshness(latest_page):
                latest_page = latest
            reported = latest_page
            if requested is not None and latest_page.get('run_attempt', 1) != requested:
                try:
                    reported = api(f'actions/runs/{run_id}/attempts/{requested}')
                except RuntimeError as error:
                    if 'HTTP 404' not in str(error):
                        raise
                    reported = None
            if (reported and (requested is None or reported.get('run_attempt', 1) == requested)
                    and reported.get('head_branch') == 'main' and reported.get('path') == '.github/workflows/pages.yml'):
                if run_freshness(reported) > run_freshness(latest_page):
                    latest_page = reported
                if reported['status'] == 'completed':
                    finished_run = reported
                    break
            if attempt < 23:
                time.sleep(5)
        else:
            errors.append(f'Pages run {run_id} attempt {requested or "latest"} did not finish in time')
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
    if latest_page is not None:
        # The latest attempt determines current status. An older event can
        # independently prove which attempt deployed the live site.
        by_id = {page['id']: page for page in pages}
        for lookup in (latest_page, finished_run):
            if lookup is None:
                continue
            listed = by_id.get(lookup['id'])
            if listed is None or run_freshness(listed) <= run_freshness(lookup):
                by_id[lookup['id']] = lookup
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
    independent_id = None
    check = checks[0] if checks else None
    check_started = run_start(check) if check else ''
    legacy_allowed = run_info(check)['state'] not in INTERRUPTED_CHECK_STATES
    def deployment(run):
        independent = run['id'] == independent_id
        if (known_success and run.get('updated_at', '') < known_success['updated_at']
                and (run['id'], run.get('run_attempt', 1)) != current_target and not independent):
            return run
        try:
            if ((independent and run.get('run_attempt', 1) == 1 or
                 'catalog_sha256' in target and (run['id'], 1) == current_target)
                    and run_info(run)['state'] == 'success'):
                return first_deployment(run)
            return publication_evidence(run)
        except (RuntimeError, KeyError, OSError, subprocess.SubprocessError) as error:
            errors.append('Deployment evidence: ' + str(error)[:350])
            if run.get('run_attempt', 1) > 1 and run.get('conclusion') == 'success':
                return dict(run, conclusion='unknown')
            return run
    try:
        reports = run_reports(check)
    except Exception as error:
        reports = {}
        errors.append('Reports: '+str(error)[:350])
    legacy = {}
    target = reports.get('publication-target.json') or {}
    eligible_targets = []
    if 'catalog_sha256' in target:
        pages = publication_receipts(pages, target, check, errors)
        eligible_targets = [page for page in pages if publication_receipt_matches(page, target)
                            or publication_dispatch_matches(page, target)]
    elif legacy_allowed:
        eligible_targets = [page for page in pages if target.get('head_sha')
                            and page.get('head_sha') == target['head_sha']
                            and (page.get('run_started_at') or page.get('created_at', '')) >= check_started]
    target_page = max(eligible_targets, key=lambda page: (page.get('run_started_at') or page.get('created_at', ''), page['id']), default=None)
    if target_page:
        current_target = (target_page['id'], target_page.get('run_attempt', 1))
    independent_page = latest_independent_page(pages, target_page['id'] if target_page else None)
    independent_id = independent_page['id'] if independent_page else None
    deployments = []
    for page in pages:
        actual = deployment(page)
        if 'catalog_sha256' in target and actual.get('run_attempt', 1) != page.get('run_attempt', 1):
            actual, = publication_receipts([actual], target, check, errors)
        deployments.append(actual)
    pages = deployments
    expected = target.get('head_sha')
    if (legacy_allowed and 'catalog_sha256' not in target and check and str(target.get('update_run_id')) == str(check['id'])
            and isinstance(expected, str) and re.fullmatch('[0-9a-f]{40}', expected)
            and not any(p.get('head_sha') == expected and run_start(p) >= check_started for p in pages)):
        matches = api('actions/workflows/pages.yml/runs?branch=main&head_sha='+expected+'&per_page=100')['workflow_runs']
        by_id = {p['id']: p for p in [*(deployment(page) for page in matches), *pages]}
        pages = sorted(by_id.values(), key=lambda p: (p['created_at'], p['id']), reverse=True)
    update = reports.get('update-report.json') or {}
    if legacy_allowed and update.get('status') == 'ok' and not reports.get('publication-target.json'):
        candidates = sorted((page for page in pages if run_start(page) >= check_started),
                            key=lambda page: (run_start(page), page['id']), reverse=True)
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
    if finished_run and not any(
            page['id'] == finished_run['id'] and page.get('run_attempt', 1) == finished_run.get('run_attempt', 1)
            for page in pages):
        last = latest_deployment([deployment(finished_run)], last)
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
