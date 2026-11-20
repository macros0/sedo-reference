"""SourceCraft and GitHub inputs shared by the status producer and UI tests."""
import json
from unittest.mock import patch

from automation_status import collect_status

UTC = '2026-11-13T21:00:00.000001Z'
OFFSET = '2026-11-14T00:00:15.000001+03:00:15'


def collect(stamp, mode):
    inputs = dict(prod='1'*64, test='2'*64)
    capture = dict(state='success', started_at=stamp, finished_at=stamp,
                   contours={env: dict(content_id=key, observed_at=stamp)
                             for env, key in inputs.items()})
    check = dict(id=10, head_sha='a'*40, head_branch='main', status='completed',
                 conclusion='success', created_at='2026-11-13T20:00:00Z', updated_at=stamp)
    page = dict(check, id=20, head_sha='b'*40, event='workflow_dispatch',
                created_at='2026-11-13T20:30:00Z',
                html_url='https://github.com/macros0/sedo-reference/actions/runs/20')
    independent = dict(page, id=21, head_sha='c'*40, event='push',
                       html_url='https://github.com/macros0/sedo-reference/actions/runs/21')
    workflows = {
        'actions/workflows/check-sfr.yml/runs?branch=main&per_page=10': {'workflow_runs': [check]},
        'actions/workflows/pages.yml/runs?branch=main&per_page=10': {'workflow_runs': [independent, page]},
    }
    reports = {
        'update-report.json': dict(status='ok', collector_source=mode, new_revisions=[], checked_at=stamp),
        'sourcecraft-report.json': {'latest': inputs},
        'publication-target.json': dict(update_run_id=10, head_sha=page['head_sha']),
    }
    with (patch('automation_status.api', side_effect=workflows.__getitem__),
          patch('automation_status.sourcecraft_status', return_value=dict(latest=capture, last_success=capture)),
          patch('automation_status.run_reports', return_value=reports),
          patch('automation_status.utcnow', return_value=stamp),
          patch('automation_status.active_check_runs', return_value=[]),
          patch('automation_status.check_history', side_effect=lambda rows: rows),
          patch('automation_status.publication_history', side_effect=lambda rows, _: rows),
          patch('automation_status.successful_prior_attempts', return_value=[]),
          patch('automation_status.publication_evidence', side_effect=lambda run, **_: run)):
        return collect_status()


if __name__ == '__main__':
    print(json.dumps({mode: [collect(UTC, mode), collect(OFFSET, mode)]
                      for mode in ('sourcecraft', 'home')}))
