import unittest
from unittest.mock import patch

from automation_status import collect_status
from tests.test_automation_status import run


class StatusHistoryTests(unittest.TestCase):
    def collect(self, historical, current=True):
        check = run(31, created='2026-11-17T08:00:00Z')
        published = dict(run(32, created='2026-11-17T08:01:00Z'), updated_at='2026-11-17T08:05:00Z')
        pages = [*([published] if current else []), *historical]
        calls = []

        def api(path):
            calls.append(path)
            if '/attempts/1' in path:
                raise RuntimeError('404 historical attempt unavailable')
            if '/jobs?' in path:
                return {'jobs': []}
            if 'check-sfr.yml' in path:
                return {'workflow_runs': [] if 'status=' in path else [check]}
            return {'workflow_runs': pages, 'total_count': len(pages)}

        reports = {'update-report.json': {'status': 'ok', 'collector_source': 'home', 'new_revisions': []},
                   'publication-target.json': {'update_run_id': 31, 'head_sha': 'a' * 40}}
        with patch('automation_status.api', side_effect=api), patch('automation_status.sourcecraft_status', return_value=None), patch('automation_status.run_reports', return_value=reports):
            return collect_status(), calls

    def test_unavailable_prior_attempt_preserves_proven_current_deployment(self):
        old = dict(run(12, conclusion='failure'), run_attempt=2, updated_at='2026-11-17T08:06:00Z')
        result, _ = self.collect([old])
        self.assertEqual(result['github']['publication']['state'], 'success')
        self.assertEqual(result['github']['last_successful_publication']['run_id'], 32)
        self.assertTrue(result['errors'])

    def test_unavailable_history_does_not_invent_a_deployment(self):
        old = dict(run(12, conclusion='failure'), run_attempt=2)
        result, _ = self.collect([old], current=False)
        self.assertNotEqual(result['github']['publication']['state'], 'success')
        self.assertEqual(result['github']['last_successful_publication']['state'], 'unknown')
        self.assertTrue(result['errors'])

    def test_old_failures_do_not_request_irrelevant_jobs(self):
        old = [run(i, sha='b' * 40, conclusion='failure') for i in range(100, 300)]
        result, calls = self.collect(old)
        self.assertEqual(result['github']['publication']['state'], 'success')
        self.assertEqual([p for p in calls if '/jobs?' in p], ['actions/runs/32/attempts/1/jobs?per_page=100&page=1'])

    def test_old_failures_for_target_sha_do_not_request_irrelevant_jobs(self):
        old = [run(i, conclusion='failure') for i in range(100, 300)]
        result, calls = self.collect(old)
        self.assertEqual(result['github']['publication']['state'], 'success')
        self.assertEqual([p for p in calls if '/jobs?' in p], ['actions/runs/32/attempts/1/jobs?per_page=100&page=1'])
