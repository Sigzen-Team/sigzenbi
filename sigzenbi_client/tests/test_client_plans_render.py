"""/client_plans (Task 10, 2026-07-10): unlimited-users rendering, current-plan
marker, and the logged-in CTA fix (a logged-in viewer must not be routed through
/portal/signup, which bounces them back to the dashboard). Verified by (a)
exercising get_context's context wiring and (b) source-inspecting the injected
plan-card JS for the render logic itself -- browser JS isn't unit-testable from
Python, but the string it's built from is."""
import inspect
import unittest
from unittest.mock import patch

import frappe

from sigzenbi_client.www import client_plans


def _gsv(doctype, field):
    return {
        "sigzenbi_erp_link": "",
        "subscription_plan_name": "Growth",
    }.get(field, "")


class TestClientPlansContext(unittest.TestCase):
    def _run(self, bi_user):
        with patch("sigzenbi_client.utils.resolve_bi_user",
                   return_value=("SID", bi_user) if bi_user else (None, None)), \
             patch("frappe.db.get_single_value", side_effect=_gsv), \
             patch("frappe.sessions.get_csrf_token", return_value="tok"), \
             patch("requests.get", side_effect=Exception("no network in unit test")):
            ctx = frappe._dict()
            client_plans.get_context(ctx)
        return ctx

    def test_logged_in_viewer_flagged(self):
        ctx = self._run("owner@x.com")
        self.assertTrue(ctx.is_logged_in)
        self.assertEqual(ctx.current_plan_name, "Growth")

    def test_anonymous_viewer_not_flagged(self):
        ctx = self._run(None)
        self.assertFalse(ctx.is_logged_in)

    def test_send_subscription_plan_wire_alias_untouched(self):
        """The frozen wire alias must not be renamed."""
        src = inspect.getsource(client_plans.get_context)
        self.assertIn(
            "sigzenbi_central.API.send_subscription_plan.send_subscription_plan", src)


class TestClientPlansCardRenderJS(unittest.TestCase):
    def setUp(self):
        self.src = inspect.getsource(client_plans.get_context)

    def test_zero_users_renders_unlimited(self):
        self.assertIn("Unlimited users", self.src)
        self.assertIn("Number(plan.custom_no_of_users) === 0", self.src)

    def test_current_plan_marker_present(self):
        self.assertIn("isCurrent", self.src)
        self.assertIn("Current Plan", self.src)

    def test_logged_in_cta_routes_to_client_billing_not_signup(self):
        self.assertIn("isLoggedIn", self.src)
        self.assertIn("/client_billing", self.src)
        # anonymous visitors still get the old (working) signup-with-plan link
        self.assertIn("/portal/signup?plan=", self.src)
