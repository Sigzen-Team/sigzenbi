"""Member-scope cache invalidation, client half (SPEC §3.2).

What must never regress:
- a permission-changing save ENQUEUES the bust and NEVER raises, even with the queue down —
  breaking the save that revoked a permission would be worse than a stale minute;
- the worker posts Central's bust endpoint once per hosted identity, per-tenant secret in the
  BODY, and skips identities with no secret;
- every network failure is swallowed (the 60 s TTL is the backstop);
- no Central URL configured -> no calls at all.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from sigzenbi_client.API.gateway import bust_scope

_PJ = "sigzenbi_client.API.gateway.poll_jobs."


class TestDocEventHalf(unittest.TestCase):
    def test_the_doc_event_enqueues_after_commit(self):
        with patch.object(frappe, "enqueue") as enq:
            bust_scope.on_permission_change(SimpleNamespace(), "on_update")
        enq.assert_called_once()
        args, kwargs = enq.call_args
        self.assertIn("bust_central_member_scope", args[0])
        self.assertTrue(kwargs.get("enqueue_after_commit"))

    def test_a_dead_queue_never_breaks_the_save(self):
        with patch.object(frappe, "enqueue", side_effect=RuntimeError("redis down")):
            bust_scope.on_permission_change(SimpleNamespace(), "on_update")  # must not raise


class TestWorkerHalf(unittest.TestCase):
    def _run(self, url="https://central.test", names=("A", "B"), secrets=None, post=None):
        secrets = secrets if secrets is not None else {"A": "sa", "B": "sb"}
        calls = []

        def _post(u, **kw):
            calls.append((u, kw))
            if post:
                post(u, kw)
            return SimpleNamespace(status_code=200)

        with patch(_PJ + "_central_url", return_value=url), \
             patch(_PJ + "_candidate_client_names", return_value=list(names)), \
             patch(_PJ + "_secret", side_effect=lambda n: secrets.get(n)), \
             patch("requests.post", side_effect=_post):
            bust_scope.bust_central_member_scope()
        return calls

    def test_posts_once_per_identity_with_the_secret_in_the_body(self):
        calls = self._run()
        self.assertEqual(len(calls), 2)
        for url, kw in calls:
            self.assertIn("bust_member_scope", url)
            self.assertIn(kw["json"]["client_name"], ("A", "B"))
            self.assertIn(kw["json"]["secret"], ("sa", "sb"))
            self.assertNotIn("sa", url)
            self.assertNotIn("sb", url)

    def test_an_identity_without_a_secret_is_skipped_not_posted_unsigned(self):
        calls = self._run(secrets={"A": "sa", "B": None})
        self.assertEqual([c[1]["json"]["client_name"] for c in calls], ["A"])

    def test_one_failing_post_does_not_stop_the_others_or_raise(self):
        def boom(url, kw):
            if kw["json"]["client_name"] == "A":
                raise ConnectionError("central briefly away")
        calls = self._run(post=boom)   # must not raise
        self.assertEqual(len(calls), 2)

    def test_no_central_url_means_no_calls(self):
        self.assertEqual(self._run(url=""), [])


if __name__ == "__main__":
    unittest.main()
