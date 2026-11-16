import unittest
from automation_status import build_status


def run(number, sha='a'*40, status='completed', conclusion='success', created='2026-11-13T08:00:00Z'):
    return dict(id=number, head_sha=sha, head_branch='main', path='.github/workflows/pages.yml', status=status, conclusion=conclusion,
                created_at=created, updated_at='2026-11-13T08:10:00Z',
                html_url='https://github.com/macros0/sedo-reference/actions/runs/'+str(number))


class AutomationStatusTests(unittest.TestCase):
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

    def test_later_standalone_pages_is_visible_after_cancelled_check(self):
        check=run(1,conclusion='cancelled',created='2026-11-13T08:00:00Z')
        for status,conclusion,state in [('completed','success','success'),('completed','failure','failure'),('in_progress',None,'processing')]:
            page=run(2,sha='b'*40,status=status,conclusion=conclusion,created='2026-11-13T09:00:00Z')
            result=build_status({},[check],[page],{}, {})
            self.assertEqual(result['github']['check']['state'],'cancelled')
            self.assertIsNone(result['github']['versions']['count'])
            self.assertEqual(result['github']['publication']['state'],state)
            self.assertEqual(result['github']['publication']['run_id'],2)

    def test_cancelled_check_keeps_target_and_older_page_unconfirmed(self):
        check=run(1,conclusion='cancelled')
        for page,target in [(run(2,created='2026-11-13T07:00:00Z'),None),
                            (run(2,sha='b'*40,created='2026-11-13T09:00:00Z'),dict(update_run_id=1,head_sha='a'*40))]:
            result=build_status({},[check],[page],{'publication-target.json':target}, {})
            self.assertEqual(result['github']['publication']['state'],'not_published')

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
                    contours={'prod':dict(observed_at='2026-11-13T07:01:00Z', changed=False),
                              'test':dict(observed_at='2026-11-13T07:02:00Z', changed=False)})),
                checks if checks is not None else [run(1)], pages or [],
                {'update-report.json':report or dict(status='ok', checked_at='2026-11-13T08:09:00Z', new_revisions=[]),
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
        result=self.result(checks=[run(1,conclusion='failure')],pages=[run(2)],
                           target=dict(update_run_id='1',head_sha='a'*40))
        self.assertEqual(result['github']['check']['state'],'failure')
        self.assertEqual(result['github']['publication']['state'],'success')

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
                    'actions/workflows/pages.yml/runs?branch=main&status=success&per_page=1': {'workflow_runs': []},
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
                    'actions/workflows/pages.yml/runs?branch=main&status=success&per_page=1': {'workflow_runs': []},
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
