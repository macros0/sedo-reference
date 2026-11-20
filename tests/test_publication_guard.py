import importlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch


class PublicationGuardTests(unittest.TestCase):
    def test_full_retry_resumes_timestamped_inbox_import_from_the_saved_commit(self):
        import build_site
        from import_inbox import process_inbox
        from publication_guard import resume_build_candidate
        from sync import sha, write
        from tests.test_fix15_sources import FilenameImportReviewTests
        from tests.test_source_collection_regressions import individual_document

        fixture = FilenameImportReviewTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        repo = fixture.root
        run_id = '123456'
        message = 'Import specifications and validated collector snapshots'
        env = dict(os.environ, GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.test',
                   GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.test')

        def git(*args):
            return subprocess.check_output(['git', *args], cwd=repo, env=env, text=True).strip()

        class Clock(datetime):
            minute = 1

            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 11, 19, 12, cls.minute, tzinfo=tz or timezone.utc)

        def import_at(minute):
            Clock.minute = minute
            with patch('import_inbox.datetime', Clock), patch('manual_import.datetime', Clock):
                return process_inbox(repo)

        git('init', '-q', '-b', 'main')
        git('config', 'core.autocrlf', 'false')
        write(repo / 'incoming/test/999.docx', individual_document('response'))
        git('add', '--', 'state', 'data', 'incoming')
        git('commit', '-qm', 'Event')
        base = git('rev-parse', 'HEAD')
        self.assertEqual(import_at(1)['new_revisions'], 1)
        first_ledger = sha((repo / 'state/inbox.json').read_bytes())
        first_catalog = sha((repo / 'site/catalog-data.js').read_bytes())
        git('add', '--', 'state', 'data')
        git('commit', '-q', '-m', message, '-m', f'SEDO-Pages-Run: {run_id}')
        saved = git('rev-parse', 'HEAD')

        # A repeat receives the event commit. Running the old job from there
        # would redetermine timestamps even with identical incoming bytes.
        git('checkout', '-q', '--detach', base)
        self.assertEqual(import_at(2)['new_revisions'], 1)
        self.assertNotEqual(sha((repo / 'state/inbox.json').read_bytes()), first_ledger)
        git('add', '--', 'state', 'data')
        git('commit', '-qm', 'New attempt')
        self.assertNotEqual(git('rev-parse', 'HEAD^{tree}'), git('rev-parse', saved + '^{tree}'))
        self.assertIsNone(resume_build_candidate(base, saved, '3', cwd=repo))
        self.assertIsNone(resume_build_candidate(base, base, run_id, cwd=repo))
        self.assertIsNone(resume_build_candidate(base, git('rev-parse', 'HEAD'), run_id, cwd=repo))
        selected = resume_build_candidate(base, saved, run_id, cwd=repo)
        self.assertEqual(selected, saved)
        git('checkout', '-q', '--detach', selected)
        self.assertEqual(import_at(3)['processed'], 0)
        self.assertEqual(sha((repo / 'state/inbox.json').read_bytes()), first_ledger)
        with patch.object(build_site, 'HERE', repo):
            build_site.main()
        self.assertEqual(sha((repo / 'site/catalog-data.js').read_bytes()), first_catalog)
        git('commit', '-q', '--allow-empty', '-m', message, '-m', f'SEDO-Pages-Run: {run_id}')
        self.assertIsNone(resume_build_candidate(base, git('rev-parse', 'HEAD'), run_id, cwd=repo))
        git('checkout', '-q', '--detach', base)
        write(repo / 'unrelated.py', b'pass\n')
        git('add', 'unrelated.py')
        git('commit', '-q', '-m', message, '-m', f'SEDO-Pages-Run: {run_id}')
        self.assertIsNone(resume_build_candidate(base, git('rev-parse', 'HEAD'), run_id, cwd=repo))

    def test_full_retry_reuses_only_the_commit_for_the_validated_tree(self):
        from publication_guard import select_build_candidate

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            env = dict(os.environ, GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.test',
                       GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.test')

            def git(*args):
                return subprocess.check_output(['git', *args], cwd=repo, env=env, text=True).strip()

            git('init', '-q')
            changed = repo / 'catalog.json'
            changed.write_text('original', 'utf8')
            git('add', 'catalog.json')
            git('commit', '-qm', 'Event')
            base = git('rev-parse', 'HEAD')

            def import_commit(message):
                git('checkout', '-q', '--detach', base)
                changed.write_text('validated import', 'utf8')
                git('commit', '-qam', message)
                return git('rev-parse', 'HEAD')

            published = import_commit('First attempt')
            retry = import_commit('Retry')
            self.assertNotEqual(published, retry)
            self.assertEqual(select_build_candidate(base, retry, base, cwd=repo), retry)
            self.assertEqual(select_build_candidate(base, retry, published, cwd=repo), published)
            self.assertEqual(select_build_candidate(base, retry, retry, cwd=repo), retry)
            git('commit', '-q', '--allow-empty', '-m', 'Move main without changing the tree')
            later = git('rev-parse', 'HEAD')
            self.assertEqual(git('rev-parse', later + '^{tree}'), git('rev-parse', retry + '^{tree}'))
            with self.assertRaisesRegex(ValueError, 'Stale'):
                select_build_candidate(base, retry, later, cwd=repo)
            git('checkout', '-q', '--detach', base)
            changed.write_text('different import', 'utf8')
            git('commit', '-qam', 'Outside change')
            with self.assertRaisesRegex(ValueError, 'Stale'):
                select_build_candidate(base, retry, git('rev-parse', 'HEAD'), cwd=repo)

    def test_build_precedes_full_suites_and_generated_site_stays_out_of_commits(self):
        workflows = Path(__file__).resolve().parents[1] / '.github/workflows'
        for filename, build in [('pages.yml', 'python build_site.py'), ('check-sfr.yml', 'python auto_update.py')]:
            content = (workflows / filename).read_text('utf8')
            before, after = content.split(build, 1)
            with self.subTest(workflow=filename):
                for suite in ('npm run test:node', 'python -m unittest discover', 'python -m tests.run_python_tests'):
                    self.assertNotIn(suite, before)
                self.assertIn('npm run test:node', after)
                self.assertIn('git add -- state data\n', after)

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
        preview = pr.split('  validate:', 1)[1]
        self.assertIn('python import_inbox.py --check', preview)
        self.assertNotIn('npm run test:node', preview)
        self.assertNotIn(gate, preview)
        self.assertEqual(pr.count(gate), 1)

    def test_deploy_job_uses_build_output_and_rechecks_main_on_every_attempt(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/pages.yml').read_text('utf8')
        deploy = workflow.split('  deploy:', 1)[1].split('  refresh-status:', 1)[0]
        self.assertIn('candidate_sha: ${{ steps.candidate.outputs.candidate_sha }}', workflow)
        self.assertIn('CANDIDATE_SHA: ${{ steps.saved-imports.outputs.candidate_sha }}', workflow)
        self.assertIn('publication_guard.py --build-candidate', workflow)
        self.assertIn('CANDIDATE_SHA: ${{ needs.build.outputs.candidate_sha }}', deploy)
        self.assertLess(deploy.index('python publication_guard.py'), deploy.index('uses: actions/deploy-pages'))
        job, steps = deploy.split('    steps:', 1)
        self.assertNotIn('continue-on-error', job)
        guard = steps.split('      - name: Reject a stale artifact', 1)[1].split('      - ', 1)[0]
        deployment = steps.split('      - name: Deploy GitHub Pages', 1)[1]
        self.assertNotIn('continue-on-error', guard)
        self.assertNotIn('continue-on-error', deployment)

    def test_pages_resumes_validated_import_before_running_collectors(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/pages.yml').read_text('utf8')
        self.assertLess(workflow.index('--resume-build'), workflow.index('python collector_snapshots.py'))
        for step in ('Validate and apply pending Russian collector snapshots', 'Import incoming specifications'):
            body = workflow.split(f'      - name: {step}\n', 1)[1].split('      - name:', 1)[0]
            self.assertIn("steps.resume-imports.outputs.sha == ''", body)
        self.assertIn('SEDO-Pages-Run: $GITHUB_RUN_ID', workflow)

    def test_main_change_during_receipt_upload_rejects_deployment(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/pages.yml').read_text('utf8')
        deploy = workflow.split('  deploy:', 1)[1].split('  refresh-status:', 1)[0]
        steps = deploy.split('\n      - ')[1:]
        candidate, latest = 'a' * 40, 'b' * 40
        current = candidate
        deployments = []
        with self.assertRaisesRegex(ValueError, '[Ss]tale'):
            for step in steps:
                if 'path: publication-receipt.json' in step:
                    current = latest
                if 'python publication_guard.py' in step:
                    self.check(candidate, current)
                if 'uses: actions/deploy-pages' in step:
                    deployments.append(candidate)
        self.assertEqual(deployments, [])
        guard = next(i for i, step in enumerate(steps) if 'python publication_guard.py' in step)
        self.assertIn('uses: actions/deploy-pages', steps[guard + 1])

    def test_rebuilt_pages_artifact_is_unique_and_deploy_retry_reuses_build_output(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/pages.yml').read_text('utf8')
        build, deploy = workflow.split('  deploy:', 1)
        deploy = deploy.split('  refresh-status:', 1)[0]
        name = re.search(r'echo "artifact_name=([^"\n]+)" >> "\$GITHUB_OUTPUT"', build)
        self.assertIsNotNone(name, 'The build must generate a name for its own Pages artifact')
        attempt_one = name[1].replace('${GITHUB_RUN_ATTEMPT}', '1')
        attempt_two = name[1].replace('${GITHUB_RUN_ATTEMPT}', '2')
        self.assertNotEqual(attempt_one, attempt_two)
        upload = build.split('uses: actions/upload-pages-artifact@', 1)[1]
        self.assertIn('name: ${{ steps.candidate.outputs.artifact_name }}', upload)
        retention = int(re.search(r'retention-days: (\d+)', upload)[1])
        self.assertGreaterEqual(retention, 30)
        self.assertIn('artifact_name: ${{ steps.candidate.outputs.artifact_name }}', build)
        self.assertIn('artifact_id: ${{ steps.pages-artifact.outputs.artifact_id }}', build)
        self.assertIn('artifact_name: ${{ needs.build.outputs.artifact_name }}', deploy)
        self.assertIn('ARTIFACT_ID: ${{ needs.build.outputs.artifact_id }}', deploy)
        for report in ('browser-test-report', 'incoming-import-report'):
            self.assertIn('name: '+report+'-${{ github.run_attempt }}', build)
