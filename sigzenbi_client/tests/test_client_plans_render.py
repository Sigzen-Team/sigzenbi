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
from unittest.mock import MagicMock, patch

import frappe

from sigzenbi_client.www import client_plans


def _gsv(doctype, field):
    return {
        "sigzenbi_erp_link": "",
    }.get(field, "")


class TestClientPlansContext(unittest.TestCase):
    def _run(self, bi_user, plan="Growth"):
        # THE CURRENT PLAN NOW COMES FROM CENTRAL, not from a local mirror field
        # (`subscription_plan_name` was removed 2026-08-16 as a stale copy). The behaviour
        # under test is the same: a signed-in tenant gets their plan flagged on the page.
        with patch("sigzenbi_client.utils.resolve_bi_user",
                   return_value=("SID", bi_user) if bi_user else (None, None)), \
             patch("frappe.db.get_single_value", side_effect=_gsv), \
             patch("frappe.sessions.get_csrf_token", return_value="tok"), \
             patch("sigzenbi_client.www.client_dashboard._fetch_subscription_state",
                   return_value={"plan": plan}), \
             patch("requests.get", side_effect=Exception("no network in unit test")):
            ctx = frappe._dict()
            client_plans.get_context(ctx)
        return ctx

    def test_central_outage_still_renders_the_page(self):
        """The plan lookup must never take the page down -- it degrades to "no plan flagged"."""
        with patch("sigzenbi_client.utils.resolve_bi_user", return_value=("SID", "owner@x.com")), \
             patch("frappe.db.get_single_value", side_effect=_gsv), \
             patch("frappe.sessions.get_csrf_token", return_value="tok"), \
             patch("sigzenbi_client.www.client_dashboard._fetch_subscription_state",
                   side_effect=Exception("central down")), \
             patch("requests.get", side_effect=Exception("no network in unit test")):
            ctx = frappe._dict()
            client_plans.get_context(ctx)          # must not raise
        self.assertEqual(ctx.current_plan_name, "")

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


class TestAdminIsSentToTheOnePlanChangeSurface(unittest.TestCase):
    """ONE surface for changing a plan: /client_billing. /client_plans is the shop
    window, so an ADMIN who lands here is redirected to the account page; a member and
    an anonymous visitor keep the read-only pricing page (billing is admin-gated and
    would only tell them it is managed by their owner).

    THE REASON THE FIRST ATTEMPT AT THIS "DID NOT FIRE" was not any of this logic: the
    page had no `no_cache`, so frappe's @cache_html served a redis copy keyed on path
    alone and get_context never ran. test_the_page_is_never_cached below is that guard --
    without it these three tests pass while the live page still ignores every one of them.
    """

    def _run(self, bi_user, can_manage, sid="SID"):
        res = None
        if can_manage is not None:
            res = MagicMock(status_code=200)
            res.json.return_value = {"message": {"can_manage": can_manage}}
        with patch("sigzenbi_client.utils.resolve_bi_user",
                   return_value=(sid, bi_user) if bi_user else (None, None)), \
             patch("sigzenbi_client.www.client_dashboard.central_get_with_sid",
                   return_value=res) as gate, \
             patch("frappe.db.get_single_value", side_effect=_gsv), \
             patch("frappe.sessions.get_csrf_token", return_value="tok"), \
             patch("requests.get", side_effect=Exception("no network in unit test")):
            ctx = frappe._dict()
            try:
                client_plans.get_context(ctx)
                redirected = None
            except frappe.Redirect:
                redirected = frappe.local.flags.redirect_location
        return redirected, gate

    def test_admin_is_redirected_to_client_billing(self):
        redirected, _ = self._run("admin@x.com", can_manage=1)
        self.assertIsNotNone(redirected, "an admin was left on the pricing page")
        self.assertTrue(str(redirected).endswith("/client_billing"), redirected)

    def test_member_keeps_the_read_only_pricing_page(self):
        redirected, _ = self._run("member@x.com", can_manage=0)
        self.assertIsNone(redirected, "a non-admin member was bounced to admin-gated billing")

    def test_anonymous_visitor_keeps_the_pricing_page_and_central_is_never_asked(self):
        redirected, gate = self._run(None, can_manage=None, sid=None)
        self.assertIsNone(redirected)
        gate.assert_not_called()

    def test_an_unreachable_central_fails_open(self):
        """No answer is not a "yes". Showing pricing is harmless; bouncing someone onto a
        page they cannot use is not."""
        redirected, _ = self._run("admin@x.com", can_manage=None)
        self.assertIsNone(redirected)

    def test_the_page_is_never_cached(self):
        """@cache_html keys on `website_page::<path>` -- path and lang, NO user. A page
        whose controller makes a per-user decision (and bakes in a per-session csrf
        token) must opt out, or one visitor's rendered page is served to everyone for 30
        minutes and get_context is not called at all."""
        with patch("sigzenbi_client.utils.resolve_bi_user", return_value=(None, None)), \
             patch("frappe.db.get_single_value", side_effect=_gsv), \
             patch("frappe.sessions.get_csrf_token", return_value="tok"), \
             patch("requests.get", side_effect=Exception("no network in unit test")):
            ctx = frappe._dict()
            client_plans.get_context(ctx)
        self.assertTrue(ctx.no_cache, "client_plans is back in the shared website_page cache")


class TestTheRetiredPlanCardJSStaysRetired(unittest.TestCase):
    """The removed card JS read a wire key Central no longer sends. If it comes back
    it must not come back reading `custom_no_of_users`, which would render every plan
    as "Unlimited users" off a key that is simply absent."""

    def test_no_read_of_the_removed_wire_key(self):
        src = inspect.getsource(client_plans)
        self.assertNotIn("plan.custom_no_of_users", src)
        self.assertNotIn("Unlimited users", src)
