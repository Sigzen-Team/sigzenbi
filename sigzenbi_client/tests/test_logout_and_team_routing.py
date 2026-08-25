"""Two regressions found by the 2026-08-06 browser sweep, both silent in production.

1. LOG OUT DID NOT LOG OUT. handleLogout POSTed without a CSRF header, so for any member who
   was ALSO signed into their own ERPNext (since SPEC 3.9, that is every member) Frappe
   answered 400 and the page swallowed it -- and even on success the surviving ERP session
   auto-SSO'd the visitor straight back in on the next page load.

2. THE ERPNEXT-USER PICKER NEVER RENDERED. team.html calls a sigzenbi_central method name;
   the mirror rewrites those to client proxies one by one, and list_erp_users was never added
   -- so on every real tenant the call hit the CLIENT origin ("App sigzenbi_central is not
   installed"), the loader gave up silently, and the admin got the free-text email box the
   picker exists to replace.
"""
import unittest

import frappe

from sigzenbi_client.www import client_login, team


class _FakeCookieManager:
	def __init__(self):
		self.deleted = []

	def delete_cookie(self, name):
		self.deleted.append(name)


class _FakeLoginManager:
	def __init__(self):
		self.logged_out = False

	def logout(self):
		self.logged_out = True


class TestLogoutEndsBothSessions(unittest.TestCase):
	def setUp(self):
		self.cookies = _FakeCookieManager()
		self.login_manager = _FakeLoginManager()
		self._orig_cookie = getattr(frappe.local, "cookie_manager", None)
		self._orig_login = getattr(frappe.local, "login_manager", None)
		self._orig_user = frappe.session.user
		frappe.local.cookie_manager = self.cookies
		frappe.local.login_manager = self.login_manager
		# addCleanup, never tearDown: a raising setUp skips tearDown and would leak the fakes.
		self.addCleanup(self._restore)

	def _restore(self):
		frappe.local.cookie_manager = self._orig_cookie
		frappe.local.login_manager = self._orig_login
		frappe.set_user(self._orig_user)

	def test_clears_bi_cookies_and_ends_the_erp_session(self):
		frappe.set_user("Administrator")          # any non-Guest stands in for a live ERP login
		client_login.logout()
		self.assertEqual(
			sorted(self.cookies.deleted),
			["central_sid", "client_session_user", "full_name"],
		)
		self.assertTrue(
			self.login_manager.logged_out,
			"a live ERP session must be ended too -- otherwise auto-SSO re-admits the visitor "
			"on the very next page load and Log out is a no-op",
		)

	def test_guest_logout_does_not_touch_the_session(self):
		frappe.set_user("Guest")
		client_login.logout()
		self.assertEqual(len(self.cookies.deleted), 3)
		self.assertFalse(self.login_manager.logged_out)


class TestTeamMirrorRoutesThePicker(unittest.TestCase):
	def test_picker_method_is_rewritten_to_the_client_proxy(self):
		import inspect

		source = inspect.getsource(team.get_context)
		self.assertIn("report_unlinked_members.list_erp_users", source)
		self.assertIn("sigzenbi_client.API.team_proxy.list_erp_users", source)

	def test_the_proxy_it_rewrites_to_actually_exists(self):
		from sigzenbi_client.API import team_proxy

		self.assertTrue(callable(getattr(team_proxy, "list_erp_users", None)))
