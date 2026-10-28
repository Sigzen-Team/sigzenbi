"""P1 UI revamp (2026-10-27): registration edge states render as cards, and the signup
proxy tags its two local failures with an additive `code` the page keys its cards on."""
import inspect
import types
import unittest
from unittest.mock import MagicMock, patch

import frappe

from sigzenbi.www.register import register


def _gsv_registered(doctype, field):
	return "AcmeCo" if field == "client_name" else ""


def _gsv_unregistered(doctype, field):
	return ""


class TestRegistrationStates(unittest.TestCase):
	def setUp(self):
		self._loc = getattr(frappe.local.flags, "redirect_location", None)
		self._user = frappe.session.user
		self.addCleanup(setattr, frappe.local.flags, "redirect_location", self._loc)
		self.addCleanup(setattr, frappe.session, "user", self._user)

	def test_registered_site_shows_the_already_registered_card_without_http(self):
		with patch("sigzenbi.www.register.register.requests") as mreq, \
				patch("frappe.get_installed_apps", return_value=["sigzenbi"]), \
				patch("frappe.db.get_single_value", side_effect=_gsv_registered):
			ctx = register.render_signup(frappe._dict())
			mreq.get.assert_not_called()
			mreq.post.assert_not_called()
		self.assertIn("This site is already registered", ctx.central_html)
		self.assertIn('href="/client_dashboard"', ctx.central_html)

	def _gate(self, user, full_name=None):
		frappe.session.user = user
		with patch("frappe.get_installed_apps", return_value=["sigzenbi"]), \
				patch("frappe.db.get_single_value", side_effect=_gsv_unregistered), \
				patch("sigzenbi.www.databasereg.databasereg.require_site_operator",
					side_effect=frappe.PermissionError), \
				patch("frappe.utils.get_fullname", return_value=full_name or user), \
				patch("sigzenbi.www.register.register.requests") as mreq:
			ctx = register.render_signup(frappe._dict())
			mreq.get.assert_not_called()
		return ctx.central_html

	def test_guest_gets_the_not_allowed_card_with_erp_sign_in(self):
		html = self._gate("Guest")
		self.assertIn("Only an ERPNext System Manager can register this company", html)
		self.assertIn("/login?redirect-to=/portal/signup", html)

	def test_signed_in_non_operator_is_named_and_escaped(self):
		html = self._gate("priya@x.com", "<b>Priya</b>")
		self.assertIn("&lt;b&gt;Priya&lt;/b&gt;", html)
		self.assertNotIn("<b>Priya", html)
		self.assertIn('href="/app"', html)

	def _post(self, existing, save_raises=None):
		resp = MagicMock()
		resp.json.return_value = {"message": {"status": "success", "client_name": "New",
			"api_key": "k", "api_secret": "s"}}
		resp.status_code = 200
		settings = MagicMock()
		settings.client_name = existing
		if save_raises:
			settings.save.side_effect = save_raises
		with patch("sigzenbi.www.databasereg.databasereg.require_site_operator"), \
				patch("sigzenbi.www.register.register.requests") as mreq, \
				patch("frappe.get_single", return_value=settings), \
				patch("frappe.db.get_single_value", return_value="https://c/"), \
				patch("frappe.db.rollback"), \
				patch("frappe.log_error"), \
				patch("sigzenbi.utils.get_client_url_and_port", return_value=("https://x", None)), \
				patch("sigzenbi.www.register.register._rollback_central_registration") as rb:
			mreq.post.return_value = resp
			r = register.get_client_credentials(client_name="New", first_name="a", email="a@b.co")
		return r, rb

	def test_rejected_reregistration_returns_code_already_registered(self):
		r, rb = self._post("Old")
		self.assertEqual(r["code"], "already_registered")
		self.assertEqual(r["status"], "error")
		rb.assert_called_once()

	def test_local_failure_returns_code_setup_failed(self):
		r, rb = self._post("", save_raises=Exception("x"))
		self.assertEqual(r["code"], "setup_failed")
		self.assertEqual(r["message"], "We couldn't finish setting up this site. Nothing was saved — please try again.")

	def test_thankyou_wires_the_setup_status_url(self):
		from sigzenbi.www import thankyou
		src = inspect.getsource(thankyou.get_context)
		self.assertIn('api_setup_status_url = "/api/method/sigzenbi.API.dashboard_api.get_analytics_token"', src)
		self.assertNotIn("<h1>Registration Successful! Thank you.</h1>", src)
