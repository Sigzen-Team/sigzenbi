"""Gateway poll loop cadence (founder decision §19, Phase 6 Task 8).

A dashboard queues one query per chart and this per-tenant loop runs them one at a time, so any
pause after a job is paid by EVERY chart in turn. The loop must ask for the next job straight away
after a cycle that ran one, and sleep IDLE_SLEEP_S only after an idle long-poll; the error
back-offs (RequestException 1 s, other 5 s) stay as they were.
"""
from unittest.mock import MagicMock, patch

import frappe
import requests
from frappe.tests.utils import FrappeTestCase

from sigzenbi.API.gateway import poll_jobs

TENANT = "p6-poll-cycle-test"


def _resp(message):
    r = MagicMock()
    r.json.return_value = {"message": message}
    r.raise_for_status.return_value = None
    return r


class TestPollLoopCycle(FrappeTestCase):
    def setUp(self):
        self.sleeps = []
        self.executed = []
        for target, value in (
            ("_acquire_singleton_lock", MagicMock(return_value=True)),
            ("_client_name", MagicMock(return_value=TENANT)),
            ("_central_url", MagicMock(return_value="https://central.invalid")),
            ("_secret", MagicMock(return_value="s3cret")),
            ("_execute_and_submit", MagicMock(side_effect=lambda *a: self.executed.append(a[3]))),
        ):
            p = patch.object(poll_jobs, target, value)
            p.start()
            self.addCleanup(p.stop)
        for p in (
            patch("time.sleep", side_effect=self.sleeps.append),
            patch.object(frappe.db, "close", lambda *a, **k: None),
            patch.object(frappe.db, "connect", lambda *a, **k: None),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(frappe.cache().delete_value, f"{poll_jobs.NO_CREDENTIAL_BACKOFF_KEY}:{TENANT}")
        self.addCleanup(frappe.cache().delete_value, f"{poll_jobs.POLL_HEARTBEAT_KEY}:{TENANT}")

    def _run(self, *responses):
        def scripted(*a, **k):
            r = responses_iter.pop(0)
            if isinstance(r, Exception):
                raise r
            return _resp(r)

        responses_iter = list(responses)
        with patch.object(poll_jobs.requests, "get", side_effect=scripted):
            poll_jobs.poll_and_execute_jobs(client_name=TENANT)
        self.assertEqual(responses_iter, [], "loop stopped before consuming every scripted response")

    def test_no_sleep_after_a_job(self):
        self._run({"job": {"job_id": "j1"}}, {"job": {"job_id": "j2"}}, {"no_credential": True})
        self.assertEqual([j["job_id"] for j in self.executed], ["j1", "j2"])
        self.assertNotIn(poll_jobs.IDLE_SLEEP_S, self.sleeps)

    def test_sleep_after_an_idle_cycle(self):
        self._run({"job": None}, {"no_credential": True})
        self.assertEqual(self.sleeps, [poll_jobs.IDLE_SLEEP_S])
        self.assertEqual(self.executed, [])

    def test_error_backoff_unchanged(self):
        self._run(requests.exceptions.RequestException("boom"), {"no_credential": True})
        self.assertEqual(self.sleeps, [1])
