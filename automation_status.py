"""Publish public operational status independently of GitHub Pages deployment."""
import base64
import io
import json
import re
import subprocess
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
             'cancelled' if run.get('conclusion') in ('cancelled', 'skipped', 'neutral') else 'failure')
    return dict(state=state, run_id=run['id'], url=run.get('html_url'),
                started_at=run.get('created_at'), finished_at=run.get('updated_at') if state != 'processing' else None)


def build_status(source, checks, pages, reports, legacy_catalogs, now=None):
    latest = (source or {}).get('latest') or {}
    sc = dict(state=latest.get('state') if latest.get('state') in ('success','failure','processing') else 'success' if latest.get('complete') else 'failure' if latest else 'unknown',
              started_at=latest.get('started_at'), finished_at=latest.get('finished_at'),
              contours=latest.get('contours', {}), run_id=latest.get('run_id'),
              url='https://sourcecraft.dev/'+SOURCE,
              last_success_at=((source or {}).get('last_success') or {}).get('finished_at'))
    if latest.get('error'):
        sc['error'] = str(latest['error'])[:500]
    check = checks[0] if checks else None
    check_info = run_info(check)
    report = reports.get('update-report.json') or {}
    complete = check_info['state'] == 'success' and report.get('status') == 'ok'
    imported = (reports.get('sourcecraft-report.json') or {}).get('latest', {})
    expected_sources = {env:c['content_id'] for env,c in sc['contours'].items() if c.get('content_id')}
    source_synced = not (latest.get('complete') and expected_sources and any(imported.get(env) != digest for env,digest in expected_sources.items()))
    if not source_synced:
        complete = False
        if check_info['state'] == 'success':
            check_info['state'] = 'waiting'
    revisions = report.get('new_revisions')
    versions = dict(count=len(revisions) if complete and isinstance(revisions, list) else None,
                    checked_at=report.get('checked_at') if complete else None)
    target = reports.get('publication-target.json') or {}
    publication = dict(state='not_published' if check_info['state'] in ('failure','cancelled') else 'waiting' if check else 'unknown')
    if check and report.get('status') == 'ok' and source_synced:
        expected = target.get('head_sha') if str(target.get('update_run_id')) == str(check['id']) else None
        candidates = [p for p in pages if p.get('created_at', '') >= check.get('created_at', '')]
        matched = next((p for p in candidates if
            (expected and p.get('head_sha') == expected) or
            (not expected and report.get('checked_at') and
             legacy_catalogs.get(p.get('head_sha')) == report['checked_at'])), None)
        if matched:
            publication = run_info(matched)
    previous = next((p for p in pages if run_info(p)['state'] == 'success'), None)
    return dict(format=1, generated_at=now or utcnow(), sourcecraft=sc,
                github=dict(check=check_info, versions=versions, publication=publication,
                            last_successful_publication=run_info(previous)))


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
    if data.get('format') != 1 or not isinstance(data.get('latest'), dict):
        raise ValueError('Invalid SourceCraft heartbeat')
    return data


def run_reports(run):
    if not run or run.get('status') != 'completed':
        return {}
    artifacts = api('actions/runs/'+str(run['id'])+'/artifacts')['artifacts']
    artifact = next((a for a in artifacts if a['name']=='sfr-update-report' and not a['expired']), None)
    if not artifact:
        return {}
    # gh follows the authenticated API redirect safely; no token is sent to the signed storage URL.
    raw = api('actions/artifacts/'+str(artifact['id'])+'/zip', binary=True)
    if len(raw) > 8*1024*1024:
        raise ValueError('Status artifact exceeds size limit')
    reports = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for name in ('update-report.json', 'sourcecraft-report.json', 'publication-target.json'):
            if name in archive.namelist():
                if archive.getinfo(name).file_size > 2*1024*1024:
                    raise ValueError('Status report exceeds size limit')
                reports[name] = json.loads(archive.read(name))
    return reports


def collect_status():
    errors = []
    try:
        source = sourcecraft_status()
    except Exception as error:
        source = None
        errors.append('SourceCraft: '+str(error)[:350])
    checks = api('actions/workflows/check-sfr.yml/runs?branch=main&per_page=10')['workflow_runs']
    pages = api('actions/workflows/pages.yml/runs?branch=main&per_page=10')['workflow_runs']
    try:
        reports = run_reports(checks[0] if checks else None)
    except Exception as error:
        reports = {}
        errors.append('Reports: '+str(error)[:350])
    legacy = {}
    update = reports.get('update-report.json') or {}
    if update.get('status') == 'ok' and not reports.get('publication-target.json'):
        for page in pages[:3]:
            digest = page.get('head_sha', '')
            if not re.fullmatch('[0-9a-f]{40}', digest) or digest in legacy:
                continue
            try:
                catalog = json.loads(read_public('https://raw.githubusercontent.com/'+REPO+'/'+digest+'/data/catalog.json'))
                legacy[digest] = catalog.get('checked_at')
            except Exception:
                legacy[digest] = None
    result = build_status(source, checks, pages, reports, legacy)
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
    status = collect_status()
    Path('automation-status-report.json').write_text(json.dumps(status, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    publish_status(status)
    print(json.dumps(dict(sourcecraft=status['sourcecraft']['state'],
        check=status['github']['check']['state'], publication=status['github']['publication']['state']), ensure_ascii=False))
