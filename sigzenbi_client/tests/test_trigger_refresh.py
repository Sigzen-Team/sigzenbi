import unittest
from unittest.mock import patch

import frappe

from sigzenbi_client.API.gateway.execute_query import trigger_refresh
from sigzenbi_client.API.gateway.poll_jobs import _candidate_client_names

# A hosted identity + its per-tenant gateway secret, both patched in. These used to be read
# from the bench: the secret from `sigzen_gateway_shared_secret` (the GLOBAL secret retired
# by the C3 cutover, so it no longer authenticates anything) and the identity from a
# hard-coded "e2etest" that only ever existed on one QA box. Between them all four tests
# skipped on every bench, so the multi-identity fix they exist to pin had no live coverage.
HOSTED = "hosted-test-identity"
SECRETS = "sigzenbi_client.API.gateway.auth._accepted_secrets"
ROSTER = "sigzenbi_client.API.gateway.poll_jobs._candidate_client_names"  # imported inside the fn
TEST_SECRET = "unit-test-per-tenant-gateway-secret"


class TestTriggerRefreshHostedIdentities(unittest.TestCase):
	"""Manual-refresh fix: trigger_refresh must accept ANY client_name this
	bench actually hosts (poll_jobs._candidate_client_names()), not just the
	single primary SigzenBI Subscription Settings.client_name — that was the
	bug making manual refresh silently no-op for every other tenant."""

	def setUp(self):
		# addCleanup, never tearDown: unittest skips tearDown when setUp raises.
		for target, kwargs in ((SECRETS, {"return_value": [TEST_SECRET]}),
		                       (ROSTER, {"return_value": ["primary-identity", HOSTED]})):
			patcher = patch(target, **kwargs)
			patcher.start()
			self.addCleanup(patcher.stop)

	def _valid_secret(self):
		return TEST_SECRET

	def test_hosted_non_primary_client_name_accepted(self):
		"""HOSTED is deliberately not first in the roster — the bug this pins accepted
		only the bench's PRIMARY identity and silently no-opped for every other tenant."""
		with patch("frappe.enqueue") as mock_enqueue:
			result = trigger_refresh(client_name=HOSTED, secret=self._valid_secret())

		self.assertEqual(result, {"queued": True})
		mock_enqueue.assert_called_once_with(
			"sigzenbi_client.API.gateway.poll_jobs.run_materialize", client_name=HOSTED
		)

	def test_unknown_client_name_rejected_even_with_valid_secret(self):
		bogus = "definitely-not-a-hosted-client-name-xyz"

		with patch("frappe.enqueue") as mock_enqueue, patch("frappe.log_error"):
			result = trigger_refresh(client_name=bogus, secret=self._valid_secret())

		self.assertEqual(result.get("success"), False)
		mock_enqueue.assert_not_called()

	def test_hosted_client_name_rejected_with_wrong_secret(self):
		with patch("frappe.enqueue") as mock_enqueue, patch("frappe.log_error"):
			result = trigger_refresh(client_name=HOSTED, secret="definitely-wrong-secret")

		self.assertEqual(result.get("success"), False)
		mock_enqueue.assert_not_called()

	def test_no_secret_rejected(self):
		with patch("frappe.enqueue") as mock_enqueue:
			result = trigger_refresh(client_name="e2etest", secret=None)

		self.assertEqual(result.get("success"), False)
		mock_enqueue.assert_not_called()
