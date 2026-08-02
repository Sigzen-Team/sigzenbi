"""/client_plans: context wiring for the logged-in CTA fix (a logged-in viewer must
not be routed through /portal/signup, which bounces them back to the dashboard).

A second class here used to source-inspect the injected plan-card JS for
"Unlimited users" / "Number(plan.custom_no_of_users) === 0" / the "Current Plan"
marker. That JS was deleted on 2026-08-02 with the plan-picker it drew, and
`custom_no_of_users` left the Central wire format on the same day -- it aliased the
deleted SigzenBI Plan.seat_cap and published 0 for every plan, which the card JS
then rendered as "Unlimited users". Asserting on a string in a comment is not a
test, so the class is gone; what remains is the regression guard below."""
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


class TestTheRetiredPlanCardJSStaysRetired(unittest.TestCase):
    """The removed card JS read a wire key Central no longer sends. If it comes back
    it must not come back reading `custom_no_of_users`, which would render every plan
    as "Unlimited users" off a key that is simply absent."""

    def test_no_read_of_the_removed_wire_key(self):
        src = inspect.getsource(client_plans)
        self.assertNotIn("plan.custom_no_of_users", src)
        self.assertNotIn("Unlimited users", src)
