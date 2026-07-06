import types
import unittest
from unittest.mock import patch

import frappe


class TestClientLoginGetContext(unittest.TestCase):
    """/client_login (retired page) must now 301 unconditionally to /portal/login,
    with the real approach-C logic living in render_bi_login."""

    def setUp(self):
        self._loc = getattr(frappe.local.flags, "redirect_location", None)

    def tearDown(self):
        frappe.local.flags.redirect_location = self._loc

    def test_client_login_get_context_301s_to_portal_login(self):
        from sigzenbi_client.www import client_login
        with self.assertRaises(frappe.Redirect):
            client_login.get_context(frappe._dict())
        self.assertTrue(
            str(frappe.local.flags.redirect_location).endswith("/portal/login"))


class TestRenderBiLogin(unittest.TestCase):
    """render_bi_login: resolved BI user -> /client_dashboard; no user -> render the
    form (show_signup toggled by whether the site is registered). Same audited
    resolve_bi_user helper; here we only verify the controller wiring."""

    def setUp(self):
        self._loc = getattr(frappe.local.flags, "redirect_location", None)

    def tearDown(self):
        frappe.local.flags.redirect_location = self._loc

    @patch("frappe.sessions.get_csrf_token", return_value="tok")
    @patch("sigzenbi_client.www.client_login.resolve_bi_user",
           return_value=("SID", "user@x.com"))
    def test_resolved_user_redirects_to_dashboard(self, _mv, _tok):
        from sigzenbi_client.www import client_login
        with self.assertRaises(frappe.Redirect):
            client_login.render_bi_login(frappe._dict())
        self.assertTrue(
            str(frappe.local.flags.redirect_location).endswith("/client_dashboard"))

    @patch("frappe.sessions.get_csrf_token", return_value="tok")
    @patch("sigzenbi_client.www.client_login.resolve_bi_user",
           return_value=(None, None))
    def test_no_user_renders_form_show_signup(self, _mv, _tok):
        from sigzenbi_client.www import client_login
        # base_url empty -> no Central HTTP fetch; client_name empty -> show_signup True.
        def _gsv(doctype, field):
            return {"sigzenbi_erp_link": "", "client_name": ""}.get(field, "")
        with patch("frappe.db.get_single_value", side_effect=_gsv):
            ctx = frappe._dict()
            client_login.render_bi_login(ctx)
        self.assertTrue(ctx.show_signup)
        self.assertTrue(ctx.central_html)  # populated (fallback placeholder is fine)

    @patch("frappe.sessions.get_csrf_token", return_value="tok")
    @patch("sigzenbi_client.www.client_login.resolve_bi_user",
           return_value=(None, None))
    def test_registered_site_hides_signup(self, _mv, _tok):
        from sigzenbi_client.www import client_login
        def _gsv(doctype, field):
            return {"sigzenbi_erp_link": "", "client_name": "AcmeCo"}.get(field, "")
        with patch("frappe.db.get_single_value", side_effect=_gsv):
            ctx = frappe._dict()
            client_login.render_bi_login(ctx)
        self.assertFalse(ctx.show_signup)


class TestRegisterGetContext(unittest.TestCase):
    """/register/register (retired page) must 301 unconditionally to /portal/signup,
    forwarding the ?plan= query string. The target path/host are static literals."""

    def setUp(self):
        self._loc = getattr(frappe.local.flags, "redirect_location", None)

    def tearDown(self):
        frappe.local.flags.redirect_location = self._loc

    def _run(self, query_string):
        from sigzenbi_client.www.register import register
        captured = {}

        def fake_redirect(path):
            captured["path"] = path
            frappe.local.flags.redirect_location = path
            raise frappe.Redirect

        req = types.SimpleNamespace(query_string=query_string)
        with patch("sigzenbi_client.utils.redirect_without_port", side_effect=fake_redirect), \
             patch.object(frappe.local, "request", req, create=True):
            with self.assertRaises(frappe.Redirect):
                register.get_context(frappe._dict())
        return captured["path"]

    def test_no_query_redirects_to_portal_signup(self):
        self.assertEqual(self._run(b""), "/portal/signup")

    def test_query_is_forwarded(self):
        self.assertEqual(self._run(b"plan=pro"), "/portal/signup?plan=pro")


class TestRenderSignup(unittest.TestCase):
    """render_signup: a registered site (client_name set) 301s to /portal/login BEFORE
    any Central HTTP call; an unregistered site does not redirect."""

    def setUp(self):
        self._loc = getattr(frappe.local.flags, "redirect_location", None)

    def tearDown(self):
        frappe.local.flags.redirect_location = self._loc

    def test_registered_site_redirects_before_http(self):
        from sigzenbi_client.www.register import register
        def _gsv(doctype, field):
            return "AcmeCo" if field == "client_name" else ""
        with patch("sigzenbi_client.www.register.register.requests") as mreq, \
             patch("frappe.get_installed_apps", return_value=["sigzenbi_client"]), \
             patch("frappe.db.get_single_value", side_effect=_gsv):
            with self.assertRaises(frappe.Redirect):
                register.render_signup(frappe._dict())
            mreq.get.assert_not_called()
            mreq.post.assert_not_called()
        self.assertEqual(frappe.local.flags.redirect_location, "/portal/login")

    def test_unregistered_site_no_redirect(self):
        from sigzenbi_client.www.register import register
        # client_name empty AND base_url empty -> no redirect, no HTTP fetch.
        def _gsv(doctype, field):
            return ""
        with patch("frappe.sessions.get_csrf_token", return_value="tok"), \
             patch("frappe.get_installed_apps", return_value=["sigzenbi_client"]), \
             patch("sigzenbi_client.www.register.register.requests") as mreq, \
             patch("frappe.db.get_single_value", side_effect=_gsv):
            ctx = frappe._dict()
            frappe.form_dict.pop("plan", None)
            register.render_signup(ctx)  # must NOT raise frappe.Redirect
            mreq.get.assert_not_called()


class TestPortalWrappers(unittest.TestCase):
    """The www/portal/* wrappers just delegate to the shared renderers."""

    def test_portal_login_delegates_to_render_bi_login(self):
        from sigzenbi_client.www.portal import login as portal_login
        sentinel = object()
        with patch("sigzenbi_client.www.portal.login.render_bi_login",
                   return_value=sentinel) as m:
            ctx = frappe._dict()
            self.assertIs(portal_login.get_context(ctx), sentinel)
            m.assert_called_once_with(ctx)

    def test_portal_signup_delegates_to_render_signup(self):
        from sigzenbi_client.www.portal import signup as portal_signup
        sentinel = object()
        with patch("sigzenbi_client.www.portal.signup.render_signup",
                   return_value=sentinel) as m:
            ctx = frappe._dict()
            self.assertIs(portal_signup.get_context(ctx), sentinel)
            m.assert_called_once_with(ctx)
