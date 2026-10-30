"""Secrets never travel in a GET query string to Central — it lands in Central's nginx access
log (2026-10-27). The per-tenant gateway secret goes in X-Sigzen-Gateway-Secret; the api pair
rides only in the Authorization header that call_central_api already sends."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from sigzenbi import utils
from sigzenbi.API.gateway import poll_jobs

_PJ = "sigzenbi.API.gateway.poll_jobs."
H = "X-Sigzen-Gateway-Secret"


class TestGatewaySecretHeader(unittest.TestCase):
    def _get(self, calls, resp):
        def _g(u, **kw):
            calls.append(kw)
            return resp
        return _g

    def test_materialize_plan_fetch_sends_secret_as_header(self):
        calls = []
        with patch(_PJ + "_central_url", return_value="https://c.test"), \
             patch(_PJ + "_secret", return_value="S"), \
             patch("requests.get", side_effect=self._get(calls, SimpleNamespace(status_code=500))), \
             patch.object(frappe, "log_error"):
            poll_jobs.run_materialize("A")
        self.assertNotIn("secret", calls[0]["params"])
        self.assertEqual(calls[0]["headers"][H], "S")

    def test_active_names_fetch_sends_secret_as_header(self):
        calls = []
        resp = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"message": {"client_names": ["A"]}})
        cache = MagicMock()
        cache.get_value.return_value = None
        with patch(_PJ + "_central_url", return_value="https://c.test"), \
             patch(_PJ + "_secret", return_value="S"), \
             patch(_PJ + "_client_name", return_value="A"), \
             patch(_PJ + "_candidate_client_names", return_value={"A"}), \
             patch.object(frappe, "cache", return_value=cache), \
             patch("requests.get", side_effect=self._get(calls, resp)):
            self.assertEqual(poll_jobs._fetch_active_client_names(), {"A"})
        self.assertNotIn("secret", calls[0]["params"])
        self.assertEqual(calls[0]["headers"][H], "S")

    def test_call_central_api_get_keeps_api_pair_out_of_query(self):
        calls = []
        resp = SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {"message": {}})
        with patch("sigzenbi.credentials.get_credentials", return_value={"key": "K", "secret": "S", "source": "doctype"}), \
             patch("requests.get", side_effect=self._get(calls, resp)):
            utils.call_central_api("https://c.test/x", payload={"a": 1}, method="GET", client_name="A")
        self.assertEqual(calls[0]["params"], {"a": 1})
        self.assertEqual(calls[0]["headers"]["Authorization"], "token K:S")
