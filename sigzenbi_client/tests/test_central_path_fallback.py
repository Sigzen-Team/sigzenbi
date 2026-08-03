"""PLAN P0.2i: the client's hardcoded Central method paths must survive Central's
API/ai regroup regardless of which box deploys first.

These are server-to-server URLs, not browser rewrites, so P0.5's template router does
not cover them. They are also strings: a wrong one is an HTTP 404 at runtime, not an
import error, and for the payment endpoints that is a captured-but-unfulfilled purchase.
"""
import unittest
from unittest.mock import MagicMock, patch

from sigzenbi_client.API import ai_proxy


class TestLegacyCentralPath(unittest.TestCase):
	def test_maps_every_new_bucket_back(self):
		cases = [
			("sigzenbi_central.API.billing.payment_api.get_available_packs",
			 "sigzenbi_central.API.ai.payment_api.get_available_packs"),
			("sigzenbi_central.API.semantic.nl2sql_api.preview_query_from_question",
			 "sigzenbi_central.API.ai.nl2sql_api.preview_query_from_question"),
			("sigzenbi_central.API.bi_chat.chat_dashboard.add_chart_to_dashboard",
			 "sigzenbi_central.API.ai.chat_dashboard.add_chart_to_dashboard"),
			("sigzenbi_central.API.ai_chat.chat_api.send_message",
			 "sigzenbi_central.API.ai.chat_api.send_message"),
		]
		for new, legacy in cases:
			with self.subTest(new=new):
				self.assertEqual(ai_proxy._legacy_central_path(new), legacy)

	def test_full_url_form_is_mapped(self):
		url = "https://central/api/method/sigzenbi_central.API.billing.byok_api.save_byok_key"
		self.assertEqual(
			ai_proxy._legacy_central_path(url),
			"https://central/api/method/sigzenbi_central.API.ai.byok_api.save_byok_key")

	def test_unrelated_paths_untouched(self):
		for path in ("sigzenbi_central.API.team.list_team.list_team",
		             "sigzenbi_central.www.client_dashboard.renew_subscription",
		             "sigzenbi_central.API.ai.payment_api.get_ledger"):
			with self.subTest(path=path):
				self.assertEqual(ai_proxy._legacy_central_path(path), path)

	def test_non_string_passthrough(self):
		self.assertIsNone(ai_proxy._legacy_central_path(None))


class TestCallCentralAiFallback(unittest.TestCase):
	NEW = "https://c/api/method/sigzenbi_central.API.billing.payment_api.get_ledger"

	def _http_error(self, status):
		exc = Exception("boom")
		exc.response = MagicMock(status_code=status)
		return exc

	def test_404_retries_once_on_legacy_path(self):
		"""Central not yet migrated -> retry the old path rather than failing."""
		with patch("sigzenbi_client.utils.call_central_api") as raw:
			raw.side_effect = [self._http_error(404), {"ok": True}]
			out = ai_proxy._call_central_ai(self.NEW)
		self.assertEqual(out, {"ok": True})
		self.assertEqual(raw.call_count, 2)
		self.assertIn(".API.ai.payment_api.get_ledger", raw.call_args_list[1].args[0])

	def test_non_404_is_not_retried(self):
		"""A retried payment call would be a DOUBLE CHARGE. Only 404 may retry."""
		with patch("sigzenbi_client.utils.call_central_api") as raw:
			raw.side_effect = self._http_error(500)
			with self.assertRaises(Exception):
				ai_proxy._call_central_ai(self.NEW)
		self.assertEqual(raw.call_count, 1)

	def test_404_on_an_already_legacy_path_does_not_loop(self):
		legacy = "https://c/api/method/sigzenbi_central.API.ai.payment_api.get_ledger"
		with patch("sigzenbi_client.utils.call_central_api") as raw:
			raw.side_effect = self._http_error(404)
			with self.assertRaises(Exception):
				ai_proxy._call_central_ai(legacy)
		self.assertEqual(raw.call_count, 1)

	def test_success_makes_exactly_one_call(self):
		with patch("sigzenbi_client.utils.call_central_api") as raw:
			raw.return_value = {"ok": 1}
			ai_proxy._call_central_ai(self.NEW)
		self.assertEqual(raw.call_count, 1)
