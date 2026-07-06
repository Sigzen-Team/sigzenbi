import unittest
from unittest.mock import patch

import frappe

from sigzenbi_client.API.gateway.execute_query import trigger_refresh
from sigzenbi_client.API.gateway.poll_jobs import _candidate_client_names


class TestTriggerRefreshHostedIdentities(unittest.TestCase):
	"""Manual-refresh fix: trigger_refresh must accept ANY client_name this
	bench actually hosts (poll_jobs._candidate_client_names()), not just the
	single primary SigzenBI Subscription Settings.client_name — that was the
	bug making manual refresh silently no-op for every other tenant."""

	def _valid_secret(self):
		secret = frappe.conf.get("sigzen_gateway_shared_secret")
		if not secret:
			self.skipTest("sigzen_gateway_shared_secret not configured on this site")
		return secret

	def test_hosted_non_primary_client_name_accepted(self):
		names = _candidate_client_names()
		if "e2etest" not in names:
			self.skipTest("e2etest is not a hosted identity on this bench")
		primary = names[0] if names else None
		if primary == "e2etest":
			self.skipTest("e2etest is this bench's primary identity; test needs a NON-primary hosted name")

		with patch("frappe.enqueue") as mock_enqueue:
			result = trigger_refresh(client_name="e2etest", secret=self._valid_secret())

		self.assertEqual(result, {"queued": True})
		mock_enqueue.assert_called_once_with(
			"sigzenbi_client.API.gateway.poll_jobs.run_materialize", client_name="e2etest"
		)

	def test_unknown_client_name_rejected_even_with_valid_secret(self):
		names = _candidate_client_names()
		bogus = "definitely-not-a-hosted-client-name-xyz"
		self.assertNotIn(bogus, names)

		with patch("frappe.enqueue") as mock_enqueue:
			result = trigger_refresh(client_name=bogus, secret=self._valid_secret())

		self.assertEqual(result.get("success"), False)
		mock_enqueue.assert_not_called()

	def test_hosted_client_name_rejected_with_wrong_secret(self):
		names = _candidate_client_names()
		if "e2etest" not in names:
			self.skipTest("e2etest is not a hosted identity on this bench")

		with patch("frappe.enqueue") as mock_enqueue:
			result = trigger_refresh(client_name="e2etest", secret="definitely-wrong-secret")

		self.assertEqual(result.get("success"), False)
		mock_enqueue.assert_not_called()

	def test_no_secret_rejected(self):
		with patch("frappe.enqueue") as mock_enqueue:
			result = trigger_refresh(client_name="e2etest", secret=None)

		self.assertEqual(result.get("success"), False)
		mock_enqueue.assert_not_called()
