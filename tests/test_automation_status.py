import unittest
from automation_status import build_status


def run(number, sha='a'*40, status='completed', conclusion='success', created='2026-11-13T08:00:00Z'):
    return dict(id=number, head_sha=sha, status=status, conclusion=conclusion,
                created_at=created, updated_at='2026-11-13T08:10:00Z',
                html_url='https://github.com/macros0/sedo-reference/actions/runs/'+str(number))


class AutomationStatusTests(unittest.TestCase):
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
