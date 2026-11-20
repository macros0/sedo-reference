import unittest
from automation_status import build_status, successful_prior_attempts


def run(number, sha='a'*40, status='completed', conclusion='success', created='2026-11-13T08:00:00Z'):
    return dict(id=number, head_sha=sha, head_branch='main', path='.github/workflows/pages.yml', status=status, conclusion=conclusion,
                created_at=created, updated_at='2026-11-13T08:10:00Z',
                html_url='https://github.com/macros0/sedo-reference/actions/runs/'+str(number))


class AutomationStatusTests(unittest.TestCase):
    def setUp(self):
        # These fixtures describe listing/publication behavior. Retry discovery
        # and attempt endpoints have their own integration tests.
        from unittest.mock import patch
        for name in ('active_check_runs', 'successful_prior_attempts'):
            stub = patch('automation_status.' + name, return_value=[])
            stub.start()
            self.addCleanup(stub.stop)
        history = patch('automation_status.check_history', side_effect=lambda checks: checks)
        history.start()
        self.addCleanup(history.stop)

    def publication_fixture(self):
        check = dict(run(10), run_attempt=2)
        pages = dict(run(20, sha='b'*40, created='2026-11-13T08:05:00Z'),
                     run_attempt=3, event='workflow_dispatch')
        target = dict(update_run_id=10, update_run_attempt=2, head_sha='a'*40,
                      catalog_sha256='c'*64)
        reports = {'update-report.json': dict(status='ok', collector_source='home', new_revisions=[]),
                   'publication-target.json': target}
        receipt = dict(pages_run_id=20, pages_run_attempt=3, candidate_sha='d'*40,
                       update_run_id=10, update_run_attempt=2, checked_sha='a'*40,
                       checked_catalog_sha256='c'*64, catalog_sha256='c'*64)
        return check, pages, reports, receipt

    def test_final_status_preserves_raw_dates_and_publishes_browser_utc(self):
        from tests.status_clock_fixture import OFFSET, UTC, collect

        status = collect(OFFSET, 'sourcecraft')
        self.assertEqual(status['errors'], [])
        source = status['sourcecraft']
        github = status['github']
        self.assertEqual(github['publication']['run_id'], 20)
        self.assertEqual(github['last_successful_publication']['run_id'], 21)
        for record, field in [
            (status, 'generated_at'), (source, 'started_at'), (source, 'finished_at'),
            (source, 'last_success_at'), (source['contours']['prod'], 'observed_at'),
            (source['contours']['test'], 'observed_at'), (github['check'], 'finished_at'),
            (github['versions'], 'checked_at'), (github['publication'], 'finished_at'),
            (github['independent_publication'], 'finished_at'),
            (github['last_successful_publication'], 'finished_at'),
        ]:
            with self.subTest(field=field, id=record.get('run_id')):
                self.assertEqual(record[field], OFFSET)
                self.assertEqual(record[field + '_utc'], UTC)

    def test_frozen_legacy_status_times_are_accepted_by_the_producer(self):
        import json
        from pathlib import Path

        from observation_time import observation_clock
        from tests.status_clock_fixture import collect

        path = Path(__file__).with_name('fixtures') / 'legacy-automation-status.json'
        for row in json.loads(path.read_bytes()):
            old = row['report']
            stamp = old['sourcecraft']['finished_at']
            with self.subTest(stamp=stamp):
                self.assertNotIn('finished_at_utc', old['sourcecraft'])
                self.assertEqual(observation_clock(stamp)['observed_at_utc'], row['utc'])
                current = collect(stamp, 'sourcecraft')
                self.assertEqual(current['sourcecraft']['finished_at_utc'], row['utc'])
                self.assertEqual(current['sourcecraft']['state'], 'success')
        for stamp in ('2021-W53-1T00:00:00Z', '2026-W46-6T00:00:00'):
            self.assertIsNone(observation_clock(stamp)['observed_at_utc'])

    def test_dispatch_of_descendant_uses_attempt_and_deployed_catalog(self):
        check, pages, reports, receipt = self.publication_fixture()
        for change, state in (({}, 'success'), ({'catalog_sha256': 'e'*64}, 'superseded'),
                              ({'update_run_id': 11}, 'waiting'), ({'update_run_attempt': 1}, 'waiting'),
                              ({'pages_run_attempt': 2}, 'waiting'), ({'checked_sha': 'd'*40}, 'waiting')):
            with self.subTest(change=change):
                candidate = dict(pages, publication_receipt=dict(receipt, **change))
                result = build_status(None, [check], [candidate], reports, {})['github']['publication']
                self.assertEqual(result['state'], state)
                if state != 'waiting':
                    self.assertEqual(result['candidate_sha'], 'd'*40)
                    self.assertEqual(result['run_attempt'], 3)

        unproven = dict(pages, head_sha=reports['publication-target.json']['head_sha'])
        self.assertEqual(build_status(None, [check], [unproven], reports, {})['github']['publication']['state'],
                         'waiting')

    def test_new_sourcecraft_capture_does_not_erase_confirmed_publication(self):
        check, pages, reports, receipt = self.publication_fixture()
        reports['update-report.json']['collector_source'] = 'sourcecraft'
        reports['sourcecraft-report.json'] = {'latest': {'prod': '1'*64, 'test': '2'*64}}
        observed = '2026-11-13T08:55:00Z'
        source = {'latest': dict(state='success', finished_at=observed, contours={
            'prod': dict(observed_at=observed, content_id='3'*64),
            'test': dict(observed_at=observed, content_id='2'*64)})}
        status = build_status(source, [check], [dict(pages, publication_receipt=receipt)], reports, {})['github']
        self.assertEqual(status['check']['state'], 'waiting')
        self.assertIsNone(status['versions']['count'])
        self.assertEqual((status['publication']['state'], status['publication']['run_id'],
                          status['publication']['candidate_sha']), ('success', 20, 'd'*40))

    def test_last_successful_inputs_remain_pending_during_the_next_capture(self):
        check, pages, reports, receipt = self.publication_fixture()
        reports['update-report.json']['collector_source'] = 'sourcecraft'
        imported = {'prod': '1'*64, 'test': '2'*64}
        reports['sourcecraft-report.json'] = {'latest': imported}
        time_b = '2026-11-13T08:55:00Z'
        ready_b = dict(state='success', finished_at=time_b, contours={
            'prod': dict(observed_at=time_b, content_id='3'*64),
            'test': dict(observed_at=time_b, content_id='2'*64),
        })
        for latest in (ready_b, dict(state='processing'), dict(state='failure', error='capture failed')):
            with self.subTest(latest=latest):
                source = dict(latest=latest, last_success=ready_b)
                status = build_status(source, [check], [dict(pages, publication_receipt=receipt)], reports, {})['github']
                self.assertEqual((status['check']['state'], status['versions']['count']), ('waiting', None))
                self.assertEqual((status['publication']['state'], status['publication']['run_id']), ('success', 20))
        imported['prod'] = '3'*64
        source = dict(latest=dict(state='failure'), last_success=ready_b)
        status = build_status(source, [check], [dict(pages, publication_receipt=receipt)], reports, {})['github']
        self.assertEqual((status['check']['state'], status['versions']['count']), ('success', 0))
        source['last_success'] = dict(finished_at=time_b)
        status = build_status(source, [check], [dict(pages, publication_receipt=receipt)], reports, {})['github']
        self.assertEqual((status['check']['state'], status['versions']['count']), ('unknown', None))
        self.assertEqual(status['publication']['state'], 'success')

    def test_independent_attempt_has_its_own_timeline_before_or_without_a_check(self):
        success = dict(run(10, created='2026-11-13T07:00:00Z'),
                       event='push', updated_at='2026-11-13T07:10:00Z')
        failure = dict(run(11, conclusion='failure', created='2026-11-13T07:15:00Z'),
                       event='push', updated_at='2026-11-13T07:18:00Z')
        correlated = dict(run(12, conclusion='failure', created='2026-11-13T09:00:00Z'),
                          event='workflow_dispatch', display_title='Publish reference for check 6 attempt 1 at '+ 'a'*40)
        for checks in ([], [run(14, status='in_progress', conclusion=None)]):
            with self.subTest(checks=checks):
                status = build_status(None, checks, [correlated, failure, success], {}, {})['github']
                self.assertEqual((status['independent_publication']['state'],
                                  status['independent_publication']['run_id']), ('failure', 11))
                self.assertEqual(status['last_successful_publication']['run_id'], 10)

    def test_collect_status_reads_receipt_for_correlated_pages_attempt(self):
        import io
        import json
        import zipfile
        from unittest.mock import patch
        from automation_status import collect_status

        check, pages, reports, receipt = self.publication_fixture()
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as output:
            output.writestr('publication-receipt.json', json.dumps(receipt))
        responses = {
            'actions/workflows/check-sfr.yml/runs?branch=main&per_page=10': {'workflow_runs': [check]},
            'actions/runs/20/artifacts?per_page=100&page=1': {'artifacts': [
                {'id': 41, 'name': 'pages-publication-receipt-2', 'expired': False},
                {'id': 42, 'name': 'pages-publication-receipt-3', 'expired': False}]},
            'actions/artifacts/42/zip': archive.getvalue(),
            'actions/runs/20/attempts/3/jobs?per_page=100&page=1': {
                'jobs': [{'name': 'deploy', 'conclusion': 'success', 'completed_at': pages['updated_at']}]},
        }
        unrelated = dict(run(22, sha='f'*40, created='2026-11-13T09:00:00Z'),
                         event='push', updated_at='2026-11-13T09:10:00Z')
        for conclusion in ('success', 'cancelled'):
            responses['actions/workflows/pages.yml/runs?branch=main&per_page=10'] = {
                'workflow_runs': [unrelated, dict(pages, conclusion=conclusion)]}
            with self.subTest(conclusion=conclusion), patch('automation_status.api', side_effect=lambda path, **_: responses[path]), \
                 patch('automation_status.sourcecraft_status', return_value={}), \
                 patch('automation_status.run_reports', return_value=reports):
                result = collect_status()
            self.assertEqual(result['errors'], [])
            self.assertEqual(result['github']['publication']['state'], 'success')
            self.assertEqual(result['github']['publication']['candidate_sha'], receipt['candidate_sha'])

    def test_correlated_dispatch_reports_early_errors_but_needs_receipt_for_success(self):
        check, pages, reports, _ = self.publication_fixture()
        title = 'Publish reference for check 10 attempt 2 at ' + 'a'*40
        for fields, state in ((dict(status='in_progress', conclusion=None), 'processing'),
                              (dict(conclusion='failure'), 'failure'),
                              (dict(conclusion='cancelled'), 'cancelled'),
                              (dict(conclusion='success'), 'unknown')):
            with self.subTest(fields=fields):
                page = dict(pages, display_title=title, **fields)
                publication = build_status(None, [check], [page], reports, {})['github']['publication']
                self.assertEqual(publication['state'], state)
        foreign = dict(pages, display_title=title.replace('attempt 2', 'attempt 1'), conclusion='failure')
        self.assertEqual(build_status(None, [check], [foreign], reports, {})['github']['publication']['state'], 'waiting')

    def test_pages_status_uses_the_attempt_that_actually_deployed(self):
        import io
        import json
        import zipfile
        from unittest.mock import patch
        from automation_status import collect_status

        check, original, reports, receipt = self.publication_fixture()
        title = 'Publish reference for check 10 attempt 2 at ' + 'a'*40
        page = dict(original, display_title=title, run_attempt=2, updated_at='2026-11-13T09:00:00Z')
        deployed_at = '2026-11-13T08:20:00Z'
        earlier = {'name': 'deploy', 'conclusion': 'success', 'completed_at': deployed_at}
        for jobs, prior_jobs, wanted, attempt, ended in (
                ([{'name': 'refresh-status', 'conclusion': 'success'}], [earlier], 'success', 1, deployed_at),
                ([dict(earlier, completed_at='2026-11-13T08:45:00Z')], [earlier], 'superseded', 2, '2026-11-13T08:45:00Z'),
                ([{'name': 'refresh-status', 'conclusion': 'success'}], None, 'unknown', 2, None),
                (None, [earlier], 'unknown', 2, None)):
            with self.subTest(jobs=jobs, prior_jobs=prior_jobs):
                archives = {}
                for n in (1, 2) if jobs and jobs[0]['name'] == 'deploy' else (1,):
                    archive = io.BytesIO()
                    with zipfile.ZipFile(archive, 'w') as z:
                        z.writestr('publication-receipt.json', json.dumps(dict(
                            receipt, pages_run_attempt=n, catalog_sha256=receipt['catalog_sha256'] if n == 1 else 'e'*64)))
                    archives[n] = archive.getvalue()

                def read(path, **kwargs):
                    if 'check-sfr.yml' in path:
                        return {'workflow_runs': [check]}
                    if 'pages.yml' in path:
                        return {'workflow_runs': [page], 'total_count': 1}
                    if '/jobs?' in path:
                        data = prior_jobs if '/attempts/1/' in path else jobs
                        if data is None:
                            raise RuntimeError('Attempt jobs are unavailable')
                        return {'jobs': data}
                    if path == 'actions/runs/20/attempts/1':
                        return dict(page, run_attempt=1, updated_at='2026-11-13T08:22:00Z')
                    if path.startswith('actions/runs/20/artifacts?'):
                        return {'artifacts': [dict(id=n, name=f'pages-publication-receipt-{n}', expired=False) for n in archives]}
                    if path.startswith('actions/artifacts/'):
                        return archives[int(path.split('/')[2])]
                    self.fail(path)

                with patch('automation_status.api', side_effect=read), patch('automation_status.sourcecraft_status', return_value=None), \
                        patch('automation_status.run_reports', return_value=reports):
                    status = collect_status()['github']
                publication = status['publication']
                self.assertEqual((publication['state'], publication['run_attempt']), (wanted, attempt))
                if ended:
                    self.assertEqual(publication['finished_at'], ended)
                    self.assertEqual(status['last_successful_publication']['finished_at'], ended)
                else:
                    self.assertNotEqual(status['last_successful_publication'].get('run_attempt'), 2)

    def test_previous_deployment_uses_completion_of_old_rerun_outside_recent_page(self):
        from automation_status import collect_status
        from unittest.mock import patch
        recent = run(17, created="2026-11-13T08:00:00Z")
        retry = dict(run(1, created="2026-11-10T05:00:00Z"), run_attempt=2, updated_at="2026-11-13T09:05:00Z")
        failures = [run(n, conclusion="failure") for n in range(30, 21, -1)]
        def read(path):
            if path == 'actions/runs/1/attempts/2/jobs?per_page=100&page=1':
                return {'jobs': [{'name': 'deploy', 'conclusion': 'success', 'completed_at': retry['updated_at']}]}
            if "check-sfr" in path:
                return {"workflow_runs": [run(31, conclusion="failure")]}
            if "status=success" in path:
                return {"workflow_runs": [recent, retry], "total_count": 2}
            return {"workflow_runs": failures + [recent], "total_count": 11}
        with patch("automation_status.api", side_effect=read), patch("automation_status.sourcecraft_status", return_value={}), patch("automation_status.run_reports", return_value={}):
            status = collect_status()["github"]
        self.assertEqual(status["publication"]["state"], "not_published")
        self.assertEqual(status["last_successful_publication"]["run_id"], 1)
        self.assertEqual(status["last_successful_publication"]["finished_at"], retry["updated_at"])

    def test_last_publication_uses_deploy_time_without_a_target_receipt(self):
        from unittest.mock import patch
        from automation_status import collect_status

        deployed_at = '2026-11-13T08:05:00Z'
        first = run(22)
        failed_retry = dict(first, run_attempt=2, conclusion='failure', updated_at='2026-11-13T09:00:00Z')
        for listed in (first, failed_retry):
            def read(path):
                if 'check-sfr.yml' in path:
                    return {'workflow_runs': []}
                if 'pages.yml' in path:
                    return {'workflow_runs': [listed], 'total_count': 1}
                if path == 'actions/runs/22/attempts/1':
                    return first
                if '/attempts/1/jobs?' in path:
                    return {'jobs': [{'name': 'deploy', 'conclusion': 'success', 'completed_at': deployed_at}]}
                if '/attempts/2/jobs?' in path:
                    return {'jobs': [{'name': 'deploy', 'conclusion': 'failure'}]}
                self.fail(path)

            with self.subTest(attempt=listed.get('run_attempt', 1)), patch('automation_status.api', side_effect=read), \
                    patch('automation_status.successful_prior_attempts', successful_prior_attempts), \
                    patch('automation_status.sourcecraft_status', return_value=None):
                last = collect_status()['github']['last_successful_publication']
            self.assertEqual((last['run_attempt'], last['finished_at']), (1, deployed_at))

    def test_long_run_history_keeps_main_success_after_the_search_window(self):
        from automation_status import collect_status
        from unittest.mock import patch
        success = dict(run(1), updated_at="2026-11-14T10:00:00Z")
        failed = run(2, conclusion="failure")
        feature = dict(success, id=4, head_branch="feature", updated_at="2026-11-14T11:00:00Z")
        def read(path):
            if 'check-sfr' in path:
                return {'workflow_runs': [failed]}
            if path.endswith('per_page=10'):
                return {'workflow_runs': [failed], 'total_count': 1001}
            if 'status=success' in path:
                return {'workflow_runs': [], 'total_count': 1001}
            if path.endswith('page=1'):
                return {'workflow_runs': [failed] * 100, 'total_count': 102}
            return {'workflow_runs': [success, feature], 'total_count': 102}
        with patch("automation_status.api", side_effect=read), patch("automation_status.sourcecraft_status", return_value={}), patch("automation_status.run_reports", return_value={}):
            self.assertEqual(collect_status()["github"]["last_successful_publication"]['run_id'], 1)

    def test_home_result_with_malformed_source_summary_uses_github_evidence(self):
        reports = {'update-report.json': dict(status='ok', collector_source='home', new_revisions=[]),
                   'publication-target.json': dict(update_run_id=1, head_sha='a'*40)}
        result = build_status({'latest': {'complete': True, 'contours': {'prod': None}}, 'last_success': ['bad']},
                              [run(1)], [run(2)], reports, {})
        self.assertEqual(result['github']['publication']['state'], 'success')
        self.assertEqual(result['sourcecraft']['state'], 'unknown')
        self.assertIn('SourceCraft', result['sourcecraft']['error'])

    def test_invalid_heartbeat_does_not_hide_github_publication(self):
        import json
        import os
        from subprocess import CompletedProcess
        from unittest.mock import patch
        from automation_status import collect_status
        for mode in ('home', 'sourcecraft'):
            for malformed in ({'latest': {'contours': None}}, {'latest': {'contours': {'prod': None}}},
                              {'latest': {}, 'last_success': []}, {'latest': {'contours': {'test': {'changed': []}}}}):
                payload = dict(format=1, **malformed)
                reports = {'update-report.json': dict(status='ok', collector_source=mode, new_revisions=[]),
                           'publication-target.json': dict(update_run_id=1, head_sha='a'*40)}
                ref = CompletedProcess([], 0, stdout='a'*40 + ' refs/heads/status\n')
                with self.subTest(mode=mode, payload=payload), patch.dict(os.environ, SEDO_COLLECTOR_SOURCE=mode), \
                        patch('automation_status.subprocess.run', return_value=ref), \
                        patch('automation_status.read_public', return_value=json.dumps(payload)), \
                        patch('automation_status.api', side_effect=[{'workflow_runs': [run(1)]}, {'workflow_runs': [run(2)]}]), \
                        patch('automation_status.run_reports', return_value=reports):
                    result = collect_status()
                    self.assertEqual(result['github']['publication']['state'], 'success')
                    self.assertEqual(result['github']['versions']['count'], 0 if mode == 'home' else None)
                    self.assertEqual(result['sourcecraft']['state'], 'unknown')
                    self.assertTrue(any('SourceCraft' in msg for msg in result['errors']))

    def test_last_success_is_queried_independently_of_failed_current_check(self):
        from automation_status import collect_status
        from unittest.mock import patch
        failures = [run(n, conclusion="failure") for n in range(20, 10, -1)]
        successful = run(9, created="2026-11-12T08:00:00Z")
        for matches in ([successful], []):
            def read(path):
                if "check-sfr" in path:
                    return {"workflow_runs": [run(21, conclusion="failure")]}
                if "status=success" in path:
                    return {"workflow_runs": matches}
                return {"workflow_runs": failures}
            with self.subTest(matches=matches), patch("automation_status.api", side_effect=read), \
                    patch("automation_status.sourcecraft_status", return_value={}), \
                    patch("automation_status.run_reports", return_value={}):
                result = collect_status()["github"]
            self.assertEqual(result["publication"]["state"], "not_published")
            self.assertEqual(result["last_successful_publication"]["state"], "success" if matches else "unknown")
            if matches:
                self.assertEqual(result["last_successful_publication"]["run_id"], 9)

    def test_later_standalone_pages_does_not_publish_the_interrupted_check(self):
        deployed = dict(run(2, sha='b'*40, created='2026-11-13T09:00:00Z'),
                        event='workflow_dispatch', display_title='Publish reference',
                        updated_at='2026-11-13T09:15:00Z')
        for conclusion in ('cancelled', 'failure'):
            check = run(1, conclusion=conclusion)
            for status, outcome, state in [('completed', 'success', 'success'),
                                           ('completed', 'failure', 'failure'),
                                           ('in_progress', None, 'processing')]:
                with self.subTest(check=conclusion, standalone=state):
                    later = dict(run(3, sha='c'*40, created='2026-11-13T10:00:00Z',
                                     status=status, conclusion=outcome), event='push',
                                 updated_at='2026-11-13T10:15:00Z')
                    other_check = dict(later, id=4, created_at='2026-11-13T11:00:00Z',
                                       event='workflow_dispatch', conclusion='failure',
                                       display_title='Publish reference for check 99 attempt 1 at ' + 'a'*40)
                    result = build_status({}, [check], [deployed, later, other_check], {}, {})['github']
                    self.assertEqual(result['check']['state'], conclusion)
                    self.assertIsNone(result['versions']['count'])
                    self.assertEqual(result['publication'], {'state': 'not_published'})
                    self.assertEqual(result['independent_publication']['state'], state)
                    self.assertEqual(result['independent_publication']['url'], later['html_url'])
                    self.assertEqual(result['last_successful_publication']['run_id'], 3 if state == 'success' else 2)

    def test_cancelled_check_keeps_target_and_older_page_unconfirmed(self):
        check=run(1,conclusion='cancelled')
        for page,target,standalone in [(run(2,created='2026-11-13T07:00:00Z'),None,'unknown'),
                            (dict(run(2,sha='b'*40,created='2026-11-13T09:00:00Z'), event='push'),
                             dict(update_run_id=1,head_sha='a'*40),'success')]:
            result=build_status({},[check],[page],{'publication-target.json':target}, {})
            self.assertEqual(result['github']['publication']['state'],'not_published')
            self.assertEqual(result['github']['independent_publication']['state'],standalone)

    def test_standalone_failed_retry_keeps_initial_deployment_live(self):
        from unittest.mock import patch
        from automation_status import collect_status

        check = run(1, conclusion='cancelled')
        first = dict(run(22, created='2026-11-13T09:00:00Z'), event='push',
                     updated_at='2026-11-13T09:15:00Z')
        next_run = dict(run(23, created='2026-11-13T10:00:00Z', conclusion='failure'), event='push',
                        updated_at='2026-11-13T10:15:00Z')
        retry = dict(first, run_attempt=2, conclusion='failure', run_started_at='2026-11-13T11:00:00Z',
                     updated_at='2026-11-13T11:15:00Z')
        deployed_at = '2026-11-13T09:10:00Z'

        def read(path):
            if 'check-sfr.yml' in path:
                return {'workflow_runs': [check]}
            if 'pages.yml' in path:
                return {'workflow_runs': [next_run, retry]}
            if path == 'actions/runs/22/attempts/1':
                return first
            if path.startswith('actions/runs/22/attempts/1/jobs?'):
                return {'jobs': [{'name': 'deploy', 'conclusion': 'success', 'completed_at': deployed_at}]}
            if path.startswith(('actions/runs/22/attempts/2/jobs?', 'actions/runs/23/attempts/1/jobs?')):
                return {'jobs': [{'name': 'deploy', 'conclusion': 'failure'}]}
            self.fail(path)

        with patch('automation_status.api', side_effect=read), patch('automation_status.run_reports', return_value={}), \
                patch('automation_status.successful_prior_attempts', successful_prior_attempts), \
                patch('automation_status.sourcecraft_status', return_value=None):
            result = collect_status()['github']

        self.assertEqual(result['publication'], {'state': 'not_published'})
        independent = result['independent_publication']
        self.assertEqual((independent['state'], independent['run_id'], independent['run_attempt']), ('failure', 22, 2))
        last = result['last_successful_publication']
        self.assertEqual((last['state'], last['run_id'], last['run_attempt'], last['finished_at']),
                         ('success', 22, 1, deployed_at))

    def test_published_target_is_found_after_ten_newer_runs(self):
        from automation_status import collect_status
        from unittest.mock import patch
        check = run(1)
        target = run(2)
        newer = [run(n, sha="b"*40) for n in range(12,2,-1)]
        def api(path):
            if "check-sfr" in path: return {"workflow_runs": [check]}
            if "head_sha=" in path: return {"workflow_runs": [target]}
            return {"workflow_runs": newer}
        reports = {"update-report.json": {"status": "ok"},
                   "publication-target.json": {"update_run_id": 1, "head_sha": "a"*40}}
        with patch("automation_status.api", side_effect=api), patch("automation_status.sourcecraft_status", return_value={}), patch("automation_status.run_reports", return_value=reports):
            result = collect_status()
        self.assertEqual(result["github"]["publication"]["state"], "success")
        self.assertEqual(result["github"]["publication"]["run_id"], 2)

    def result(self, checks=None, pages=None, report=None, target=None, legacy=None):
        return build_status(dict(latest=dict(complete=True, finished_at='2026-11-13T07:02:00Z',
                    contours={'prod':dict(observed_at='2026-11-13T07:01:00Z', changed=False, content_id='a'*64),
                              'test':dict(observed_at='2026-11-13T07:02:00Z', changed=False, content_id='a'*64)})),
                checks if checks is not None else [run(1)], pages or [],
                {'update-report.json':report or dict(status='ok', checked_at='2026-11-13T08:09:00Z', new_revisions=[]),
                 'sourcecraft-report.json':dict(latest={'prod':'a'*64,'test':'a'*64}),
                 'publication-target.json':target}, legacy or {}, now='2026-11-13T09:00:00Z')

    def test_previous_publication_does_not_prove_new_update_published(self):
        result=self.result(pages=[run(2,'b'*40,created='2026-11-13T06:00:00Z')],
                           target=dict(update_run_id='1', head_sha='a'*40))
        self.assertEqual(result['github']['publication']['state'],'waiting')
        self.assertEqual(result['github']['versions']['count'],0)

    def test_version_count_uses_schema_events_with_legacy_report_fallback(self):
        for report, expected in ((dict(new_schema_revisions=[], new_revisions=[{'revision_id':'new-source'}]), 0),
                                 (dict(new_schema_revisions=[{'revision_id':'schema'}], new_revisions=[{},{}]), 1),
                                 (dict(new_revisions=[{}]), 1)):
            with self.subTest(report=report):
                result = self.result(report=dict(status='ok', **report))
                self.assertEqual(result['github']['versions']['count'], expected)

    def test_matching_publication_success_failure_and_processing(self):
        for status,conclusion,state in [('completed','success','success'),('completed','failure','failure'),('in_progress',None,'processing')]:
            result=self.result(pages=[run(2,status=status,conclusion=conclusion)],
                               target=dict(update_run_id='1',head_sha='a'*40))
            self.assertEqual(result['github']['publication']['state'],state)

    def test_legacy_publication_requires_matching_catalog_timestamp(self):
        result=self.result(pages=[run(2)],legacy={'a'*40:'2026-11-13T08:09:00Z'})
        self.assertEqual(result['github']['publication']['state'],'success')
        result=self.result(pages=[run(2)],legacy={'a'*40:'2026-11-12T08:09:00Z'})
        self.assertEqual(result['github']['publication']['state'],'waiting')

    def test_legacy_matching_sha_or_catalog_time_does_not_link_an_interrupted_check(self):
        page = dict(run(2, created='2026-11-13T09:00:00Z'), event='push', updated_at='2026-11-13T09:15:00Z')
        for conclusion in ('cancelled', 'failure'):
            for target, catalogs in ((dict(update_run_id=1, head_sha='a'*40), {}),
                                     (None, {'a'*40: '2026-11-13T08:09:00Z'})):
                with self.subTest(conclusion=conclusion, target=target):
                    report = self.result(checks=[run(1, conclusion=conclusion)], pages=[page],
                                         target=target, legacy=catalogs)['github']
                    self.assertEqual(report['check']['state'], conclusion)
                    self.assertEqual(report['publication'], {'state': 'not_published'})
                    self.assertEqual(report['independent_publication']['run_id'], 2)
                    self.assertEqual(report['last_successful_publication']['run_id'], 2)

    def test_running_or_failed_check_does_not_report_zero_new_versions(self):
        for status,conclusion in [('in_progress',None),('completed','failure')]:
            result=self.result(checks=[run(1,status=status,conclusion=conclusion)])
            self.assertIsNone(result['github']['versions']['count'])
            self.assertNotEqual(result['github']['publication']['state'],'success')

    def test_sourcecraft_check_time_not_schema_first_observation(self):
        result=self.result()
        self.assertEqual(result['sourcecraft']['contours']['prod']['observed_at'],'2026-11-13T07:01:00Z')
        self.assertFalse(result['sourcecraft']['contours']['test']['changed'])

    def test_new_sourcecraft_inputs_are_not_confirmed_by_old_check(self):
        result=build_status(dict(latest=dict(complete=True,contours={'prod':dict(content_id='new')})),
            [run(1)],[run(2)],{'update-report.json':dict(status='ok',checked_at='x',new_revisions=[]),
            'sourcecraft-report.json':dict(latest={'prod':'old'}),
            'publication-target.json':dict(update_run_id='1',head_sha='a'*40)}, {})
        self.assertEqual(result['github']['check']['state'],'waiting')
        self.assertIsNone(result['github']['versions']['count'])
        self.assertEqual(result['github']['publication']['state'],'waiting')

    def test_home_collector_results_do_not_wait_for_sourcecraft(self):
        reports = {'update-report.json': dict(status='ok', collector_source='home', new_revisions=[]),
                   'publication-target.json': dict(update_run_id='1', head_sha='a'*40)}
        result = build_status(dict(latest=dict(complete=True, contours={'prod': dict(content_id='old')})),
                              [run(1)], [run(2)], reports, {})
        self.assertEqual(result['collector_source'], 'home')
        self.assertEqual(result['github']['check']['state'], 'success')
        self.assertEqual(result['github']['publication']['state'], 'success')

    def test_in_progress_home_mode_visible_before_check_writes_report(self):
        result = build_status({}, [run(1, status='in_progress', conclusion=None)], [], {}, {},
                              configured_source='home')
        self.assertEqual(result['collector_source'], 'home')
        self.assertEqual(result['github']['check']['state'], 'processing')

    def test_artifact_reads_only_named_json_reports(self):
        import io,json,zipfile
        from unittest.mock import patch
        from automation_status import run_reports
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as archive:
            archive.writestr('update-report.json',json.dumps(dict(status='ok',new_revisions=[])))
            archive.writestr('sourcecraft-report.json',json.dumps(dict(latest={'prod':'id'})))
            archive.writestr('../ignored.py','raise RuntimeError()')
        with patch('automation_status.api',side_effect=[dict(artifacts=[dict(id=5,name='sfr-update-report',expired=False)]),out.getvalue()]):
            reports=run_reports(run(1))
        self.assertEqual(set(reports),{'update-report.json','sourcecraft-report.json'})

    def test_sourcecraft_started_run_is_processing(self):
        result=build_status(dict(latest=dict(state='processing',complete=False,started_at='2026-11-13T09:00:00Z',contours={})),[],[],{}, {})
        self.assertEqual(result['sourcecraft']['state'],'processing')

    def test_post_publication_check_failure_does_not_hide_actual_deployment(self):
        check, page, reports, receipt = self.publication_fixture()
        check = dict(check, conclusion='failure')
        page = dict(page, publication_receipt=receipt)
        result = build_status(None, [check], [page], reports, {})
        self.assertEqual(result['github']['check']['state'],'failure')
        self.assertEqual(result['github']['publication']['state'],'success')
        self.assertEqual(result['github']['publication']['candidate_sha'], receipt['candidate_sha'])

    def test_final_refresh_reads_finished_run_when_workflow_list_still_shows_processing(self):
        from unittest.mock import patch
        from automation_status import collect_status
        check = run(1)
        pending = run(2, status='in_progress', conclusion=None)
        for conclusion in ('success', 'failure', 'cancelled'):
            with self.subTest(conclusion=conclusion):
                final = run(2, conclusion=conclusion)
                responses = {
                    'actions/workflows/check-sfr.yml/runs?branch=main&per_page=10': {'workflow_runs': [check]},
                    'actions/workflows/pages.yml/runs?branch=main&per_page=10': {'workflow_runs': [pending]},
                    'actions/runs/2': iter([pending, final]),
                    'actions/workflows/pages.yml/runs?branch=main&status=success&per_page=100&page=1': {'workflow_runs': []},
                }

                def read(path):
                    value = responses[path]
                    return next(value) if path == 'actions/runs/2' else value

                reports = {'publication-target.json': {'update_run_id': 1, 'head_sha': check['head_sha']},
                           'update-report.json': {'status': 'ok', 'new_revisions': []}}
                with patch('automation_status.api', side_effect=read), patch('time.sleep'), \
                     patch('automation_status.sourcecraft_status', return_value={}), \
                     patch('automation_status.run_reports', return_value=reports):
                    publication = collect_status(publication_run_id=2)['github']['publication']
                self.assertEqual(publication['state'], conclusion)
                self.assertEqual(publication['finished_at'], final['updated_at'])

    def test_final_refresh_merges_missing_run_and_preserves_newer_matches(self):
        from automation_status import collect_status
        from unittest.mock import patch

        check = run(1)
        final = run(2)
        newer = run(3, created='2026-11-13T08:05:00Z', conclusion='failure')
        for listed, direct, wanted in (
                ([], final, 2), ([newer], final, 3),
                ([], dict(final, head_branch='feature'), None),
                ([], dict(final, path='.github/workflows/check-sfr.yml'), None),
                ([dict(final, run_attempt=2, status='in_progress', conclusion=None)],
                 dict(final, run_attempt=1), 2)):
            with self.subTest(listed=listed, direct=direct):
                responses = {
                    'actions/workflows/check-sfr.yml/runs?branch=main&per_page=10': {'workflow_runs': [check]},
                    'actions/workflows/pages.yml/runs?branch=main&per_page=10': {'workflow_runs': listed},
                    'actions/runs/2': direct,
                    'actions/workflows/pages.yml/runs?branch=main&status=success&per_page=100&page=1': {'workflow_runs': []},
                    'actions/workflows/pages.yml/runs?branch=main&head_sha='+'a'*40+'&per_page=100': {'workflow_runs': []},
                }
                reports = {'publication-target.json': {'update_run_id': 1, 'head_sha': check['head_sha']},
                           'update-report.json': {'status': 'ok', 'new_revisions': []}}
                with patch('automation_status.api', side_effect=responses.__getitem__), \
                     patch('automation_status.sourcecraft_status', return_value={}), \
                     patch('automation_status.run_reports', return_value=reports):
                    result = collect_status(2)['github']['publication']
                self.assertEqual(result.get('run_id'), wanted)
                if listed and listed[0].get('run_attempt') == 2:
                    self.assertEqual(result['state'], 'processing')

    def test_finished_pages_event_preserves_deploy_while_newer_attempt_runs(self):
        from unittest.mock import patch
        from automation_status import collect_status

        check = run(1)
        finished = dict(run(2), run_attempt=1, updated_at='2026-11-13T08:12:00Z')
        for status, conclusion in (('in_progress', None), ('completed', 'cancelled')):
            with self.subTest(status=status):
                newer = dict(finished, run_attempt=2, status=status, conclusion=conclusion,
                             run_started_at='2026-11-13T08:13:00Z', updated_at='2026-11-13T08:15:00Z')
                attempts = iter([RuntimeError('GitHub API: HTTP 404'), finished])
                def read(path):
                    if path == 'actions/runs/2/attempts/1':
                        value = next(attempts)
                        if isinstance(value, Exception):
                            raise value
                        return value
                    if path == 'actions/runs/2':
                        return newer
                    if 'check-sfr.yml' in path:
                        return {'workflow_runs': [check]}
                    if 'pages.yml' in path:
                        return {'workflow_runs': []}
                    raise KeyError(path)

                reports = {'publication-target.json': {'update_run_id': 1, 'head_sha': check['head_sha']},
                           'update-report.json': {'status': 'ok', 'new_revisions': []}}
                with patch('automation_status.api', side_effect=read), patch('automation_status.time.sleep') as wait, \
                     patch('automation_status.sourcecraft_status', return_value={}), \
                     patch('automation_status.run_reports', return_value=reports):
                    result = collect_status(publication_run_id=2, publication_run_attempt=1)['github']
                wait.assert_called_once()
                self.assertEqual(result['publication']['run_attempt'], 2)
                self.assertEqual(result['publication']['state'], 'processing' if status == 'in_progress' else 'cancelled')
                self.assertEqual(result['last_successful_publication']['run_attempt'], 1)


class HeartbeatIdentityTests(unittest.TestCase):
    def test_success_requires_both_current_contour_identities(self):
        stamp = '2026-11-17T08:00:00Z'
        for complete in (None, True):
            for identities in (False, True):
                latest = dict(state='success', finished_at=stamp,
                              contours={env:dict(observed_at=stamp, **({'content_id':'b'*64} if identities else {})) for env in ('prod','test')})
                if complete is not None:
                    latest['complete'] = complete
                reports = {'update-report.json':dict(status='ok', new_revisions=[]),
                           'sourcecraft-report.json':{'latest':{env:'a'*64 for env in ('prod','test')}}}
                result = build_status({'latest':latest}, [run(1)], [], reports, {})
                self.assertEqual(result['github']['check']['state'], 'waiting')
                self.assertIsNone(result['github']['versions']['count'])
                if identities:
                    reports['sourcecraft-report.json']['latest'] = {env:'b'*64 for env in ('prod','test')}
                    self.assertEqual(build_status({'latest':latest},[run(1)],[],reports,{})['github']['versions']['count'],0)


class MissingHeartbeatTests(unittest.TestCase):
    def test_missing_source_does_not_confirm_version_count(self):
        report={'update-report.json':dict(status='ok',collector_source='sourcecraft',new_revisions=[]),
                'publication-target.json':dict(update_run_id=1,head_sha='a'*40)}
        result=build_status(None,[run(1)],[run(2)],report,{})
        self.assertIsNone(result['github']['versions']['count'])
        self.assertEqual(result['github']['publication']['state'],'success')
