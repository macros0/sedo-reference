import io
import json
import unittest
import zipfile
from unittest.mock import patch

from automation_status import build_status, collect_status, run_info, run_reports, check_history, publication_history
from tests.test_automation_status import run


class AttemptStatusTests(unittest.TestCase):
    def test_event_attempt_wins_over_stale_run_listing(self):
        old = run(21)
        current = dict(old, run_attempt=2, status='in_progress', conclusion=None,
                       path='.github/workflows/check-sfr.yml', run_started_at='2026-11-17T08:00:00Z')
        def api(path):
            if path == 'actions/runs/21':
                return dict(old, path='.github/workflows/check-sfr.yml')
            if path == 'actions/runs/21/attempts/2':
                return current
            return dict(workflow_runs=[old] if 'check-sfr' in path else [run(35)], total_count=1)
        with patch('automation_status.api', side_effect=api), patch('automation_status.sourcecraft_status', return_value=None):
            result = collect_status(check_run_id=21, check_run_attempt=2)
        self.assertEqual(result['github']['check']['state'], 'processing')
        self.assertEqual(result['github']['check']['run_attempt'], 2)

    def test_stale_active_listing_cannot_reopen_finished_check_from_event(self):
        finished = dict(run(21, conclusion='failure'), path='.github/workflows/check-sfr.yml',
                        run_attempt=2, run_started_at='2026-11-17T08:00:00Z',
                        updated_at='2026-11-17T08:05:00Z')
        processing = dict(finished, status='in_progress', conclusion=None)

        def api(path):
            if path == 'actions/runs/21':
                return finished
            if 'check-sfr.yml' in path:
                rows = [processing] if 'status=' not in path or 'status=in_progress&' in path else []
                return dict(workflow_runs=rows)
            return dict(workflow_runs=[])

        with patch('automation_status.api', side_effect=api), patch('automation_status.sourcecraft_status', return_value=None), \
                patch('automation_status.run_reports', return_value={}):
            result = collect_status(check_run_id='21')
        check = result['github']['check']
        self.assertEqual((check['run_id'], check['run_attempt'], check['state']), (21, 2, 'failure'))
        self.assertEqual(result['errors'], [])

    def test_current_publication_finds_later_retry_even_when_recent_sha_matches(self):
        check = dict(run(21), run_started_at='2026-11-15T08:00:00Z')
        published = dict(run(15, created='2026-11-16T08:00:00Z'), updated_at='2026-11-16T08:02:00Z')
        others = [run(i, sha='b'*40, created='2026-11-15T09:00:00Z') for i in range(100, 109)]
        recent = [published, *others]
        retry = dict(run(6, conclusion='failure'), run_attempt=2, run_started_at='2026-11-17T08:00:00Z',
                     updated_at='2026-11-17T08:01:00Z')
        reports = {'update-report.json': dict(status='ok'),
                   'publication-target.json': dict(update_run_id=21, head_sha=published['head_sha'])}

        def api(path):
            if '/jobs?' in path:
                return {'jobs': []}
            if 'check-sfr.yml' in path:
                return dict(workflow_runs=[] if 'status=' in path else [check])
            if path == 'actions/runs/6/attempts/1':
                return run(6)
            if path.endswith('per_page=10'):
                return dict(workflow_runs=recent, total_count=11)
            if 'status=success' in path:
                return dict(workflow_runs=recent, total_count=10)
            if path == 'actions/workflows/pages.yml/runs?per_page=100&page=1':
                return dict(workflow_runs=[*recent, retry], total_count=11)
            self.fail(f'Unexpected API request {path}')

        with patch('automation_status.api', side_effect=api), patch('automation_status.sourcecraft_status', return_value=None), \
                patch('automation_status.run_reports', return_value=reports):
            result = collect_status()
        publication = result['github']['publication']
        self.assertEqual((publication['run_id'], publication['run_attempt'], publication['state']), (6, 2, 'failure'))
        self.assertEqual(result['github']['last_successful_publication']['run_id'], 15)
        self.assertEqual(result['errors'], [])

    def test_completed_old_retry_remains_current_on_scheduled_refresh(self):
        recent = run(11, created='2026-11-16T08:00:00Z')
        retry = dict(run(1, conclusion='failure'), run_attempt=2,
                     run_started_at='2026-11-17T08:00:00Z')
        def api(path):
            if 'check-sfr' in path:
                return dict(workflow_runs=[] if 'status=' in path else [recent] if path.endswith('per_page=10') else [recent, retry])
            return dict(workflow_runs=[run(50)], total_count=1)
        with patch('automation_status.api', side_effect=api), patch('automation_status.sourcecraft_status', return_value=None):
            result = collect_status()
        self.assertEqual(result['github']['check']['run_id'], 1)
        self.assertEqual(result['github']['check']['state'], 'failure')

    def test_latest_publication_attempt_matches_by_start_time(self):
        check = dict(run(21), run_started_at='2026-11-15T08:00:00Z')
        earlier = run(5, created='2026-11-16T08:00:00Z')
        retry = dict(run(6, conclusion='failure'), run_attempt=2, run_started_at='2026-11-17T08:00:00Z')
        result = build_status(None, [check], [earlier, retry], {
            'update-report.json': dict(status='ok'),
            'publication-target.json': dict(update_run_id=21, head_sha='a'*40)}, {})
        self.assertEqual(result['github']['publication']['run_id'], 6)
        self.assertEqual(result['github']['publication']['state'], 'failure')

    def test_previous_successful_attempt_is_found_outside_recent_page(self):
        recent = run(100)
        failed = dict(run(50, conclusion='failure'), run_attempt=2)
        successful = dict(run(50), run_attempt=1, updated_at='2026-11-17T08:00:00Z')
        def api(path):
            if 'check-sfr' in path:
                return dict(workflow_runs=[])
            if path == 'actions/runs/50/attempts/1':
                return successful
            if path.endswith('per_page=10'):
                return dict(workflow_runs=[recent], total_count=11)
            if 'status=success' in path:
                return dict(workflow_runs=[recent], total_count=1)
            return dict(workflow_runs=[recent, failed], total_count=11)
        with patch('automation_status.api', side_effect=api), patch('automation_status.sourcecraft_status', return_value=None):
            result = collect_status()
        self.assertEqual(result['github']['last_successful_publication']['run_id'], 50)
        self.assertEqual(result['github']['last_successful_publication']['run_attempt'], 1)

    def test_old_run_retry_is_current_check_and_keeps_previous_successful_attempt(self):
        retry = dict(run(10, status='in_progress', conclusion=None), run_attempt=2,
                     run_started_at='2026-11-17T08:00:00Z')
        yesterday = run(11, created='2026-11-16T08:00:00Z')
        failed_page = dict(run(50, conclusion='failure'), run_attempt=2)
        success_page = dict(run(50), run_attempt=1, updated_at='2026-11-16T09:00:00Z')
        older_page = run(49)
        def api(path):
            if path == 'actions/runs/50/attempts/1':
                return success_page
            if 'check-sfr.yml' in path:
                return dict(workflow_runs=[retry] if 'status=' in path else [yesterday, retry])
            return dict(workflow_runs=[failed_page, older_page], total_count=2)
        with patch('automation_status.api', side_effect=api), patch('automation_status.sourcecraft_status', return_value=None):
            result = collect_status()
        self.assertEqual(result['github']['check']['state'], 'processing')
        self.assertEqual(result['github']['check']['run_id'], 10)
        self.assertEqual(result['github']['last_successful_publication']['run_id'], 50)
        self.assertEqual(result['github']['last_successful_publication']['run_attempt'], 1)

    def test_retry_does_not_read_first_attempt_artifact(self):
        retry = dict(run(21, conclusion='failure'), run_attempt=2)
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as archive:
            archive.writestr('update-report.json', json.dumps(dict(status='ok')))
        with patch('automation_status.api', side_effect=[{'artifacts': [
                dict(id=91, name='sfr-update-report', expired=False)]}, out.getvalue()]) as api:
            self.assertEqual(run_reports(retry), {})
            self.assertEqual(api.call_count, 1)

    def test_current_attempt_report_is_read(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as archive:
            archive.writestr('update-report.json', json.dumps(dict(status='ok')))
        with patch('automation_status.api', side_effect=[{'artifacts': [
                dict(id=92, name='sfr-update-report-2', expired=False)]}, out.getvalue()]):
            self.assertEqual(run_reports(dict(run(21), run_attempt=2))['update-report.json']['status'], 'ok')

    def test_retry_started_at_uses_attempt_time(self):
        retry = dict(run(10), run_started_at='2026-11-17T08:00:00Z', run_attempt=2)
        self.assertEqual(run_info(retry)['started_at'], retry['run_started_at'])

    def test_retry_target_cannot_confirm_previous_attempt(self):
        retry = dict(run(21), run_attempt=2, run_started_at='2026-11-17T08:00:00Z')
        result = build_status(None, [retry], [run(35)], {
            'update-report.json': dict(status='ok'),
            'publication-target.json': dict(update_run_id=21, update_run_attempt=1, head_sha='a'*40)}, {})
        self.assertNotEqual(result['github']['publication']['state'], 'success')


class HistoryFreshnessTests(unittest.TestCase):
    def test_terminal_same_attempt_replaces_stale_initial_listing(self):
        old = dict(run(21, status='in_progress', conclusion=None), run_attempt=2, updated_at='2026-11-17T08:00:00Z')
        finished = dict(old, status='completed', conclusion='success', updated_at='2026-11-17T08:05:00Z')
        previous = dict(finished, run_attempt=1, updated_at='2026-11-17T09:00:00Z')
        for history in (lambda rows: check_history(rows), lambda rows: publication_history(rows, True)):
            with self.subTest(history=history), patch('automation_status.api', return_value={'workflow_runs':[finished, previous], 'total_count':2}):
                selected = history([old])[0]
            self.assertEqual((selected['run_attempt'], selected['status']), (2, 'completed'))

    def test_collect_uses_completed_state_read_in_history(self):
        old=dict(run(21,status='in_progress',conclusion=None),run_attempt=2,updated_at='2026-11-17T08:00:00Z')
        finished=dict(old,status='completed',conclusion='success',updated_at='2026-11-17T08:05:00Z')
        def api(path):
            if 'check-sfr' in path:
                if 'status=' in path:return {'workflow_runs':[old] if 'status=in_progress&' in path else []}
                return {'workflow_runs':[old] if path.endswith('per_page=10') else [finished], 'total_count':1}
            return {'workflow_runs':[run(35)],'total_count':1}
        with patch('automation_status.api',side_effect=api), patch('automation_status.sourcecraft_status',return_value=None), patch('automation_status.run_reports',return_value={}):
            result=collect_status()
        self.assertEqual(result['github']['check']['state'],'success')
        self.assertEqual(result['github']['check']['run_attempt'],2)
        self.assertEqual(result['errors'],[])

    def test_success_heartbeat_requires_all_dates(self):
        from automation_status import validate_heartbeat
        good={'latest':{'state':'success','finished_at':'2026-11-17T08:00:00Z','contours':{env:{'observed_at':'2026-11-17T07:59:00Z'} for env in ('prod','test')}}}
        import copy
        for key in ('finished_at','prod','test'):
            data=copy.deepcopy(good)
            if key=='finished_at':data['latest'].pop(key)
            else:data['latest']['contours'][key].pop('observed_at')
            with self.subTest(key=key), self.assertRaises(ValueError):validate_heartbeat(data)
