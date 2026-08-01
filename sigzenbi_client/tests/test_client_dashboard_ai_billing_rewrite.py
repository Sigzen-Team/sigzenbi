"""2026-07-10: client_dashboard's rewrite chain must cover every Central AI
billing/BYOK method (and the Renew button's method) -- root CLAUDE.md: no browser
request may hit the Central domain.

2026-08-01 (PLAN P0.5/P0.2k): this asserted the rewrite pairs by grepping
get_context's SOURCE for literal strings. That coupled the guard to one
implementation and, worse, would have kept passing if the literals were present
but the Central paths had moved -- a str.replace() whose key is absent is a
silent no-op. The AI rewrites now live in utils.route_ai_methods_to_proxy, so
this asserts BEHAVIOUR: run representative html through the real chain and check
what comes out. It now also covers the post-regroup buckets, which the old
source-grep could not express at all.
"""
import unittest
from unittest.mock import patch

from sigzenbi_client.utils import route_ai_methods_to_proxy
from sigzenbi_client.www import client_dashboard

# (central method, expected client method). Old bucket = today; new buckets = after
# Central's API/ai regroup. Both must route, so the two boxes can deploy independently.
AI_REWRITE_PAIRS = [
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
    # post-regroup buckets
    ("sigzenbi_central.API.billing.payment_api.get_available_packs",
     "sigzenbi_client.API.ai_proxy.get_available_packs"),
    ("sigzenbi_central.API.billing.byok_api.get_ai_billing_status",
     "sigzenbi_client.API.ai_proxy.get_ai_billing_status"),
]

# Not an AI method -- rewritten by client_dashboard itself, not by the helper.
RENEW_PAIR = ("sigzenbi_central.www.client_dashboard.renew_subscription",
              "sigzenbi_client.www.client_dashboard.renew_subscription")


class TestClientDashboardAiBillingRewrite(unittest.TestCase):
    def test_ai_methods_route_to_client_proxy(self):
        for central_method, client_method in AI_REWRITE_PAIRS:
            with self.subTest(central=central_method):
                html = f'frappe.call({{method: "{central_method}"}});'
                out = route_ai_methods_to_proxy(html)
                self.assertIn(client_method, out)
                self.assertNotIn(central_method, out,
                                 f"{central_method} still points at Central")

    def test_no_central_domain_method_survives(self):
        """The whole point: nothing sigzenbi_central.API.<bucket>.<aimodule> may
        remain in html handed to the browser."""
        html = "\n".join(f'"{m}"' for m, _ in AI_REWRITE_PAIRS)
        out = route_ai_methods_to_proxy(html)
        for bucket in ("ai", "billing", "semantic", "bi_chat", "ai_chat"):
            for module in ("payment_api", "byok_api", "nl2sql_api", "chat_api", "chat_dashboard"):
                self.assertNotIn(f"sigzenbi_central.API.{bucket}.{module}", out)

    def test_renew_subscription_still_rewritten_by_page(self):
        """Still a literal replace in client_dashboard -- it is a www path, not an
        AI method, so the helper must NOT claim it."""
        import inspect
        src = inspect.getsource(client_dashboard.get_context)
        central_method, client_method = RENEW_PAIR
        self.assertIn(f'"{central_method}"', src)
        self.assertIn(f'"{client_method}"', src)
        self.assertEqual(route_ai_methods_to_proxy(central_method), central_method)

    def test_page_calls_the_shared_router(self):
        """Guard against the rewrites being reintroduced inline and drifting again."""
        import inspect
        src = inspect.getsource(client_dashboard.get_context)
        self.assertIn("route_ai_methods_to_proxy(central_html)", src)


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
