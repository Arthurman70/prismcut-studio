"""Tests for core.jobs - the background job/thread-pool wrapper."""
import pytest
from PySide6.QtWidgets import QApplication

from prismcut.core.jobs import Job, _Runner


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_runner_emits_failed_when_job_was_cancelled_before_it_ever_started(app):
    """Bug fix: a job cancelled while still sitting in the QThreadPool's
    internal queue (never actually started) used to return from
    _Runner.run() with NO signal emitted at all - neither finished nor
    failed - permanently wedging any caller counting completions (e.g.
    pipeline_orchestrator._BatchTracker, which never reaches zero unless
    every job reports in one way or the other)."""
    job = Job("test job", lambda j: "should never run")
    job.cancel()
    assert job.status == "cancelled"

    failures = []
    job.failed.connect(failures.append)
    finishes = []
    job.finished.connect(finishes.append)

    _Runner(job).run()   # call synchronously - no real thread pool needed to test this

    assert failures == ["Cancelled"]
    assert finishes == []
    assert job.status == "cancelled"


def test_runner_still_runs_normally_when_job_was_not_cancelled(app):
    job = Job("test job", lambda j: "result")
    finishes = []
    job.finished.connect(finishes.append)

    _Runner(job).run()

    assert finishes == ["result"]
    assert job.status == "done"


def test_runner_emits_failed_when_job_is_cancelled_mid_run():
    """Distinct from the queued-cancellation case above: a job that starts
    running and THEN gets cancelled (checked via job.check_cancelled()
    inside fn, or observed right after fn returns) already emitted
    'failed' correctly before this fix - covered here so both
    cancellation branches in _Runner.run() have a test."""
    job = Job("test job", lambda j: (j.cancel(), "ignored")[1])
    failures = []
    job.failed.connect(failures.append)

    _Runner(job).run()

    assert failures == ["Cancelled"]
    assert job.status == "cancelled"
