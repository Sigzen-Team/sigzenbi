"""P1 UI revamp (2026-10-27): the calm paywall (SPEC S11, mock Paywall). Renders the real
www/paywall.html through the pure paywall_context -- no I/O, no Central."""
import unittest

import frappe

from sigzenbi.www import client_dashboard

PLANS = [{"name": "Flat", "cost": 1000, "billing_interval": "Month"}]


def _render(state, plans=PLANS, **kw):
	ctx = client_dashboard.paywall_context(state, plans, **kw)
	with open(frappe.get_app_path("sigzenbi", "www", "paywall.html"), encoding="utf-8") as f:
		return frappe.render_template(f.read(), ctx)


class TestPaywallRender(unittest.TestCase):
	def test_paywall_context_trial_owner(self):
		html = _render({"status": "Expired", "is_trial": True, "end_date": "2026-10-24"})
		for s in ("Trial ended", "Your trial has ended", "24 Oct 2026", 'href="/client_billing?plan=Flat"', "₹1,000", "Choose a plan"):
			self.assertIn(s, html)

	def test_paywall_context_expired_owner(self):
		html = _render({"status": "Expired", "is_trial": False, "end_date": "2026-10-24"})
		self.assertIn("Subscription expired", html)
		self.assertIn("Renew plan", html)

	def test_paywall_context_member(self):
		html = _render({"status": "Expired", "is_trial": True, "end_date": "2026-10-24"}, is_member=True)
		self.assertIn("Only your organisation admin can choose a plan", html)
		self.assertIn('href="/app"', html)
		self.assertNotIn("/client_billing?plan=", html)

	def test_paywall_context_bi_not_in_plan(self):
		html = _render({"status": "Active", "is_trial": False}, bi_missing=True)
		self.assertIn("Not in your plan", html)
		self.assertIn("Add seats", html)

	def test_paywall_context_empty_plans(self):
		html = _render({"status": "Expired", "is_trial": True}, plans=[])
		self.assertIn("Plans are loading. Please refresh in a moment, or contact support.", html)

	def test_paywall_context_no_end_date(self):
		html = _render({"status": "Expired", "is_trial": True, "end_date": None})
		self.assertNotIn("ended on", html)

	def test_no_invented_numbers(self):
		html = _render({"status": "Expired", "is_trial": True, "end_date": "2026-10-24"})
		for s in ("30 days", "Reminder sent", "Talk to us"):
			self.assertNotIn(s, html)

	def test_app_css_linked_from_central(self):
		html = _render({"status": "Expired", "is_trial": True}, central_url="https://c/")
		self.assertIn('href="https://c/assets/sigzenbi_central/css/app.css"', html)
