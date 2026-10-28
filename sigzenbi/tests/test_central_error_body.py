"""CHAT-E2E-FIXES check-error-body (2026-10-27): a Central refusal (417) reached the browser with the full
Python traceback, including the chained requests error (Central host, endpoint, client_name, chat_user).
The browser gets Central's message only."""
import json
from unittest.mock import MagicMock, patch

import frappe
import requests
from frappe.tests.utils import FrappeTestCase

from sigzenbi import utils


def _refusal(text="You're sending messages too quickly."):
	r = MagicMock(status_code=417)
	r.json.return_value = {"_server_messages": json.dumps([json.dumps({"message": text})])}
	r.raise_for_status.side_effect = requests.exceptions.HTTPError(
		"417 Client Error: EXPECTATION FAILED for url: https://central.example/api/method/x?client_name=acme&chat_user=a@b.c")
	return r


class TestCentralRefusalIsClean(FrappeTestCase):
	def _call(self, response):
		self.addCleanup(setattr, frappe.local.flags, "disable_traceback", frappe.local.flags.get("disable_traceback"))
		with patch("sigzenbi.credentials.get_credentials", return_value={"key": "k", "secret": "s", "source": "doctype"}), \
				patch.object(utils.requests, "post", return_value=response):
			utils.call_central_api("https://central.example/api/method/x", payload={}, client_name="acme")

	def test_the_message_is_centrals_and_nothing_is_chained(self):
		with self.assertRaises(frappe.ValidationError) as cm:
			self._call(_refusal())
		self.assertIn("too quickly", str(cm.exception))
		self.assertTrue(cm.exception.__suppress_context__)        # no "During handling of ... HTTPError"
		self.assertTrue(frappe.local.flags.disable_traceback)      # no traceback in the response body

	def test_no_message_still_raises_the_http_error(self):
		r = _refusal()
		r.json.return_value = {}
		with self.assertRaises(requests.exceptions.HTTPError):
			self._call(r)
