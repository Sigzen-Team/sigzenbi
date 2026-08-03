"""Client proxies for credit-pack purchase + BYOK self-serve (2026-07-10).
Each proxy must forward to the right Central method with client_name always
server-derived (via _get_client_name()), never taken from the caller's args.

2026-07-10 security fix: the 7 billing-mutating/reading proxies below were
originally built on call_central_api, which signs requests with the tenant's
API key (the org OWNER's identity) -- any roster member calling them from the
client site could act as the owner (initiate purchases, read the ledger,
change BYOK keys/policy). They now forward the caller's own `central_sid`
cookie via team_proxy._forward (no Authorization header), so Central's
payment_api._assert_client_access / byok_api._current_client re-derive the
real caller from the session and deny non-owners. See ai_proxy.py and
team_proxy.py's HARD RULE docstring.
"""
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sigzenbi_client.API import ai_proxy

CALL = "sigzenbi_client.utils.call_central_api"  # imported lazily inside each proxy fn
FORWARD = "sigzenbi_client.API.team_proxy._forward"  # imported lazily inside each sid-forwarded proxy fn


class TestAIProxyBilling(FrappeTestCase):
    # --- unchanged: global/non-sensitive, still call_central_api (tenant API key) ---

    def test_get_available_packs_forwards(self):
        with patch(CALL, return_value=[{"name": "Starter"}]) as m:
            out = ai_proxy.get_available_packs()
        called_url = m.call_args[0][0]
        self.assertIn("payment_api.get_available_packs", called_url)
        self.assertEqual(out, [{"name": "Starter"}])

    def test_get_wallet_balance_forwards(self):
        with patch(CALL, return_value={"balance": 100}) as m:
            out = ai_proxy.get_wallet_balance()
        called_url = m.call_args[0][0]
        self.assertIn("payment_api.get_wallet_balance", called_url)
        self.assertEqual(out, {"balance": 100})

    # --- 2026-07-10 security fix: sid-only forwarding via team_proxy._forward ---
    # PAYMENT endpoints: client_name IS forwarded so Central re-verifies ownership
    # against the sid session (payment_api._assert_client_access).

    def test_initiate_purchase_sid_forwards_with_client_name(self):
        with patch(FORWARD, return_value={"order_id": "o1"}) as m:
            out = ai_proxy.initiate_razorpay_purchase("Starter")
        method_path, payload = m.call_args[0]
        self.assertEqual(method_path, "sigzenbi_central.API.ai.payment_api.initiate_razorpay_purchase")
        self.assertEqual(payload["pack_name"], "Starter")
        self.assertEqual(payload["client_name"], ai_proxy._get_client_name())
        self.assertEqual(out, {"order_id": "o1"})

    def test_get_purchase_history_sid_forwards_with_client_name(self):
        with patch(FORWARD, return_value=[]) as m:
            ai_proxy.get_purchase_history(limit=10)
        method_path, payload = m.call_args[0]
        self.assertEqual(method_path, "sigzenbi_central.API.ai.payment_api.get_purchase_history")
        self.assertEqual(payload["limit"], 10)
        self.assertEqual(payload["client_name"], ai_proxy._get_client_name())

    def test_get_ledger_sid_forwards_with_client_name(self):
        with patch(FORWARD, return_value=[]) as m:
            ai_proxy.get_ledger(limit=5)
        method_path, payload = m.call_args[0]
        self.assertEqual(method_path, "sigzenbi_central.API.ai.payment_api.get_ledger")
        self.assertEqual(payload["limit"], 5)
        self.assertEqual(payload["client_name"], ai_proxy._get_client_name())
        # offset always forwarded (default 0); types/allowance omitted when falsy so an
        # empty filter reads as "no filter" to Central, not as an impossible one.
        self.assertEqual(payload["offset"], 0)
        self.assertNotIn("types", payload)
        self.assertNotIn("allowance", payload)

    def test_get_ledger_forwards_the_timeline_filters(self):
        """get_ledger grew offset/types/allowance on Central for the ledger timeline
        rewrite (2026-08-03). This proxy builds an EXPLICIT payload rather than
        splatting kwargs, so an unforwarded param vanishes silently -- the purse/type
        filter would appear to do nothing, with no error."""
        with patch(FORWARD, return_value={"rows": [], "total": 0}) as m:
            ai_proxy.get_ledger(limit=25, offset=25, types='["Grant","Purchase"]', allowance="build")
        method_path, payload = m.call_args[0]
        self.assertEqual(method_path, "sigzenbi_central.API.ai.payment_api.get_ledger")
        self.assertEqual(payload["offset"], 25)
        self.assertEqual(payload["types"], '["Grant","Purchase"]')
        self.assertEqual(payload["allowance"], "build")
        self.assertEqual(payload["client_name"], ai_proxy._get_client_name())

    # BYOK endpoints: client_name is NOT forwarded -- Central's byok_api derives it
    # from session.user server-side; passing it would be an unexpected kwarg.

    def test_save_byok_key_sid_forwards_and_never_logs(self):
        with patch(FORWARD, return_value={"ok": True, "last4": "1234"}) as m:
            out = ai_proxy.save_byok_key("sk-ant-SECRET1234")
        method_path, payload = m.call_args[0]
        self.assertEqual(method_path, "sigzenbi_central.API.ai.byok_api.save_byok_key")
        self.assertEqual(payload, {"api_key": "sk-ant-SECRET1234"})
        self.assertNotIn("client_name", payload)
        self.assertEqual(out["last4"], "1234")

    def test_remove_byok_key_sid_forwards_no_client_name(self):
        with patch(FORWARD, return_value={"ok": True}) as m:
            ai_proxy.remove_byok_key()
        method_path, payload = m.call_args[0]
        self.assertEqual(method_path, "sigzenbi_central.API.ai.byok_api.remove_byok_key")
        self.assertEqual(payload, {})

    def test_set_ai_policy_sid_forwards_no_client_name(self):
        with patch(FORWARD, return_value={"ok": True}) as m:
            ai_proxy.set_ai_policy("credits_then_byok")
        method_path, payload = m.call_args[0]
        self.assertEqual(method_path, "sigzenbi_central.API.ai.byok_api.set_ai_policy")
        self.assertEqual(payload, {"policy_order": "credits_then_byok"})

    def test_get_ai_billing_status_sid_forwards_no_client_name(self):
        with patch(FORWARD, return_value={"policy_order": "credits_then_byok"}) as m:
            out = ai_proxy.get_ai_billing_status()
        method_path, payload = m.call_args[0]
        self.assertEqual(method_path, "sigzenbi_central.API.ai.byok_api.get_ai_billing_status")
        self.assertEqual(payload, {})
        self.assertEqual(out["policy_order"], "credits_then_byok")

    def test_api_key_never_logged(self):
        """save_byok_key must never pass the raw key to frappe.log_error -- grep-level
        regression guard: patch log_error and assert it's never called on the happy
        path (the only place a secret could leak into logs)."""
        with patch(FORWARD, return_value={"ok": True}), patch("frappe.log_error") as log:
            ai_proxy.save_byok_key("sk-ant-SECRET1234")
        log.assert_not_called()

    # --- fail-closed: no central_sid (no request context) -> PermissionError ---
    # Exercises the REAL _forward (not mocked): frappe.local.request is unset in a
    # bench test/console context, so _forward must throw before any HTTP call --
    # proving the write proxies don't silently proceed without a session.

    def test_write_proxy_without_sid_fails_closed(self):
        for fn, args in (
            (ai_proxy.initiate_razorpay_purchase, ("Starter",)),
            (ai_proxy.save_byok_key, ("sk-ant-x",)),
            (ai_proxy.remove_byok_key, ()),
            (ai_proxy.set_ai_policy, ("credits_then_byok",)),
        ):
            with self.assertRaises(frappe.PermissionError, msg=f"{fn.__name__} did not fail closed"):
                fn(*args)
