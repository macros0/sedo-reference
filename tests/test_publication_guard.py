import importlib
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch


class PublicationGuardTests(unittest.TestCase):
    def test_queued_updates_are_retained_and_checkout_current_main(self):
        root = Path(__file__).resolve().parents[1]
        for workflow in ('pages.yml', 'check-sfr.yml'):
            content = (root/'.github/workflows'/workflow).read_text('utf8')
            with self.subTest(workflow=workflow):
                self.assertIn('  queue: max\n', content)
        check = (root/'.github/workflows/check-sfr.yml').read_text('utf8')
        self.assertIn('- uses: actions/checkout@v4\n        with:\n          ref: main\n', check)

    def check(self, candidate, current):
        self.assertIsNotNone(importlib.util.find_spec('publication_guard'),
                             'Deployment must revalidate the built commit against live main')
        guard = importlib.import_module('publication_guard')
        response = io.BytesIO(json.dumps({'object': {'sha': current}}).encode())
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/reference', 'GH_TOKEN': 'test-token',
                                     'GITHUB_SHA': 'e' * 40}), patch.object(guard, 'urlopen', return_value=response):
            return guard.ensure_current(candidate)

    def test_a_then_b_then_retry_of_a_cannot_publish_stale_artifact(self):
        self.assertEqual(self.check('a' * 40, 'a' * 40), 'a' * 40)
        self.assertEqual(self.check('b' * 40, 'b' * 40), 'b' * 40)
        with self.assertRaisesRegex(ValueError, '[Ss]tale'):
            self.check('a' * 40, 'b' * 40)

    def test_workflow_created_commit_is_checked_instead_of_event_sha(self):
        self.assertEqual(self.check('c' * 40, 'c' * 40), 'c' * 40)

    def test_invalid_remote_sha_cannot_approve_deployment(self):
        with self.assertRaises(ValueError):
            self.check('a' * 40, '')

    def test_candidate_invariants_run_after_build_in_the_pr_job(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/validate-incoming.yml').read_text('utf8')
        browser = workflow.split('  browser:', 1)[1].split('  validate:', 1)[0]
        gate = 'python -m tests.run_python_tests --jobs 2'
        self.assertIn(gate, browser)
        self.assertLess(browser.index('python build_site.py'), browser.index(gate))

    def test_full_python_run_has_headroom_and_precedes_publication(self):
        root = Path(__file__).resolve().parents[1] / '.github/workflows'
        gate = 'python -m tests.run_python_tests --jobs 2'
        daily = (root / 'check-sfr.yml').read_text('utf8')
        self.assertIn('timeout-minutes: 120', daily)
        for prerequisite in ('python auto_update.py', 'python verify.py'):
            self.assertLess(daily.index(prerequisite), daily.index(gate))
        for gated in ('npm run test:browser', 'git push origin HEAD:main', 'gh workflow run pages.yml'):
            self.assertLess(daily.index(gate), daily.index(gated))
        pr = (root / 'validate-incoming.yml').read_text('utf8')
        self.assertEqual(pr.count('timeout-minutes: 120'), 2)
        self.assertEqual(pr.count(gate), 2)

    def test_deploy_job_uses_build_output_and_rechecks_main_on_every_attempt(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/pages.yml').read_text('utf8')
        deploy = workflow.split('  deploy:', 1)[1].split('  refresh-status:', 1)[0]
        self.assertIn('candidate_sha: ${{ steps.candidate.outputs.candidate_sha }}', workflow)
        self.assertIn('candidate_sha=$(git rev-parse HEAD)', workflow)
        self.assertIn('CANDIDATE_SHA: ${{ needs.build.outputs.candidate_sha }}', deploy)
        self.assertLess(deploy.index('python publication_guard.py'), deploy.index('uses: actions/deploy-pages'))
        job, steps = deploy.split('    steps:', 1)
        self.assertNotIn('continue-on-error', job)
        guard = steps.split('      - name: Reject a stale artifact', 1)[1].split('      - ', 1)[0]
        deployment = steps.split('      - name: Deploy GitHub Pages', 1)[1]
        self.assertNotIn('continue-on-error', guard)
        self.assertNotIn('continue-on-error', deployment)
