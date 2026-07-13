"""2026-07-10: client_dashboard's rewrite map must cover every new Central AI
billing/BYOK method (and the Renew button's method) -- root CLAUDE.md: no browser
request may hit the Central domain. Source-inspects the rewrite chain (robust,
no HTTP/render mocking needed) per the plan's "unit-assert the rewrite map
contains the new keys" verification step."""
import inspect
import unittest
from unittest.mock import patch

from sigzenbi_client.www import client_dashboard

REWRITE_PAIRS = [
    ("sigzenbi_central.API.ai.payment_api.get_available_packs",
     "sigzenbi_client.API.ai_proxy.get_available_packs"),
    ("sigzenbi_central.API.ai.payment_api.initiate_razorpay_purchase",
     "sigzenbi_client.API.ai_proxy.initiate_razorpay_purchase"),
    ("sigzenbi_central.API.ai.payment_api.get_purchase_history",
     "sigzenbi_client.API.ai_proxy.get_purchase_history"),
    ("sigzenbi_central.API.ai.payment_api.get_ledger",
     "sigzenbi_client.API.ai_proxy.get_ledger"),
    ("sigzenbi_central.API.ai.byok_api.save_byok_key",
     "sigzenbi_client.API.ai_proxy.save_byok_key"),
    ("sigzenbi_central.API.ai.byok_api.remove_byok_key",
     "sigzenbi_client.API.ai_proxy.remove_byok_key"),
    ("sigzenbi_central.API.ai.byok_api.set_ai_policy",
     "sigzenbi_client.API.ai_proxy.set_ai_policy"),
    ("sigzenbi_central.API.ai.byok_api.get_ai_billing_status",
     "sigzenbi_client.API.ai_proxy.get_ai_billing_status"),
    ("sigzenbi_central.www.client_dashboard.renew_subscription",
     "sigzenbi_client.www.client_dashboard.renew_subscription"),
]


class TestClientDashboardAiBillingRewrite(unittest.TestCase):
    def test_rewrite_map_covers_new_ai_billing_methods(self):
        src = inspect.getsource(client_dashboard.get_context)
        for central_method, client_method in REWRITE_PAIRS:
            self.assertIn(f'"{central_method}"', src,
                          f"missing rewrite source for {central_method}")
            self.assertIn(f'"{client_method}"', src,
                          f"missing rewrite target for {central_method}")


class TestRenewSubscriptionProxy(unittest.TestCase):
    """renew_subscription must forward via team_proxy's sid-only _forward -- never
    utils.call_central_api's tenant-API-key auth, which would authenticate every
    caller as the org owner (see client_dashboard.renew_subscription's docstring)."""

    @patch("sigzenbi_client.API.team_proxy._forward", return_value={"order_id": "o1"})
    def test_forwards_to_central_renew_subscription(self, mock_forward):
        out = client_dashboard.renew_subscription()
        mock_forward.assert_called_once_with(
            "sigzenbi_central.www.client_dashboard.renew_subscription", {})
        self.assertEqual(out, {"order_id": "o1"})
