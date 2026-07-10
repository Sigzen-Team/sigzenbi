"""Client proxies for credit-pack purchase + BYOK self-serve (2026-07-10).
Each proxy must forward to the right Central method with client_name always
server-derived (via _get_client_name()), never taken from the caller's args."""
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sigzenbi_client.API import ai_proxy

CALL = "sigzenbi_client.utils.call_central_api"  # imported lazily inside each proxy fn


class TestAIProxyBilling(FrappeTestCase):
    def test_get_available_packs_forwards(self):
        with patch(CALL, return_value=[{"name": "Starter"}]) as m:
            out = ai_proxy.get_available_packs()
        called_url = m.call_args[0][0]
        self.assertIn("payment_api.get_available_packs", called_url)
        self.assertEqual(out, [{"name": "Starter"}])

    def test_initiate_purchase_forwards_pack(self):
        with patch(CALL, return_value={"order_id": "o1"}) as m:
            ai_proxy.initiate_razorpay_purchase("Starter")
        called_url = m.call_args[0][0]
        self.assertIn("payment_api.initiate_razorpay_purchase", called_url)
        payload = m.call_args.kwargs["payload"]
        self.assertEqual(payload["pack_name"], "Starter")
        self.assertEqual(payload["client_name"], ai_proxy._get_client_name())

    def test_get_purchase_history_forwards(self):
        with patch(CALL, return_value=[]) as m:
            ai_proxy.get_purchase_history(limit=10)
        called_url = m.call_args[0][0]
        self.assertIn("payment_api.get_purchase_history", called_url)
        self.assertEqual(m.call_args.kwargs["payload"]["limit"], 10)

    def test_get_ledger_forwards(self):
        with patch(CALL, return_value=[]) as m:
            ai_proxy.get_ledger(limit=5)
        called_url = m.call_args[0][0]
        self.assertIn("payment_api.get_ledger", called_url)
        self.assertEqual(m.call_args.kwargs["payload"]["limit"], 5)

    def test_save_byok_key_forwards_and_never_logs(self):
        with patch(CALL, return_value={"ok": True, "last4": "1234"}) as m:
            out = ai_proxy.save_byok_key("sk-ant-SECRET1234")
        called_url = m.call_args[0][0]
        self.assertIn("byok_api.save_byok_key", called_url)
        self.assertEqual(m.call_args.kwargs["payload"]["api_key"], "sk-ant-SECRET1234")
        # client_name is server-derived, never accepted as a payload key here
        self.assertNotIn("client_name", m.call_args.kwargs["payload"])
        self.assertEqual(out["last4"], "1234")

    def test_remove_byok_key_forwards(self):
        with patch(CALL, return_value={"ok": True}) as m:
            ai_proxy.remove_byok_key()
        called_url = m.call_args[0][0]
        self.assertIn("byok_api.remove_byok_key", called_url)

    def test_set_ai_policy_forwards(self):
        with patch(CALL, return_value={"ok": True}) as m:
            ai_proxy.set_ai_policy("credits_then_byok")
        called_url = m.call_args[0][0]
        self.assertIn("byok_api.set_ai_policy", called_url)
        self.assertEqual(m.call_args.kwargs["payload"]["policy_order"], "credits_then_byok")

    def test_get_ai_billing_status_forwards(self):
        with patch(CALL, return_value={"policy_order": "credits_then_byok"}) as m:
            out = ai_proxy.get_ai_billing_status()
        called_url = m.call_args[0][0]
        self.assertIn("byok_api.get_ai_billing_status", called_url)
        self.assertEqual(out["policy_order"], "credits_then_byok")

    def test_all_proxies_pass_client_name_kwarg_for_credential_selection(self):
        """call_central_api's client_name kwarg selects which tenant identity's
        credentials sign the request (see utils.call_central_api docstring) --
        every new proxy must pass it, not rely on the singleton fallback."""
        for fn, args in (
            (ai_proxy.get_available_packs, ()),
            (ai_proxy.initiate_razorpay_purchase, ("Starter",)),
            (ai_proxy.get_purchase_history, ()),
            (ai_proxy.get_ledger, ()),
            (ai_proxy.save_byok_key, ("sk-ant-x",)),
            (ai_proxy.remove_byok_key, ()),
            (ai_proxy.set_ai_policy, ("credits_then_byok",)),
            (ai_proxy.get_ai_billing_status, ()),
        ):
            with patch(CALL, return_value={}) as m:
                fn(*args)
            self.assertEqual(
                m.call_args.kwargs.get("client_name"), ai_proxy._get_client_name(),
                f"{fn.__name__} did not pass client_name to call_central_api",
            )
