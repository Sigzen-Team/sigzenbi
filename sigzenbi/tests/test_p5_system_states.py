"""P5 (UI revamp 2026-10-27): every SigzenBI dead end is a complete, self-contained, calm page.

guided_fallback / system_page (utils), the analytics hand-off interstitial + failure page, and
render errors that used to serve Central's raw un-rendered template."""
import ast
import importlib
import inspect
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from werkzeug.wrappers import Response

from sigzenbi import utils
from sigzenbi.API import analytics_handoff as AH


class TestGuidedFallback(FrappeTestCase):
    def test_fallback_is_a_complete_self_contained_page(self):
        h = utils.guided_fallback("Your dashboard", True)
        self.assertTrue(h.startswith("<!DOCTYPE html>"))
        for s in ('<html lang="en">', 'name="viewport"', "</html>", "prefers-reduced-motion", ":focus-visible"):
            self.assertIn(s, h)
        self.assertNotIn("http://", h)
        self.assertNotIn("https://", h)

    def test_unavailable_variant(self):
        h = utils.guided_fallback("Your dashboard", True)
        for s in ("Your dashboard is temporarily unavailable", ">Try again<", 'href=""', 'href="/app"',
                  "mailto:support@sigzen.com"):
            self.assertIn(s, h)

    def test_not_configured_variant(self):
        h = utils.guided_fallback("Team", False)
        self.assertIn("Team is not available yet", h)
        self.assertNotIn("Try again", h)

    def test_label_is_escaped(self):
        h = utils.guided_fallback("<i>x</i>", True)
        self.assertIn("&lt;i&gt;x&lt;/i&gt;", h)
        self.assertNotIn("<i>x</i>", h)

    def test_no_vendor_word(self):
        for c in (True, False):
            self.assertNotIn("superset", utils.guided_fallback("X", c).lower())


class TestAnalyticsHandoffStates(FrappeTestCase):
    def setUp(self):
        old = frappe.local.response
        frappe.local.response = frappe._dict()
        self.addCleanup(setattr, frappe.local, "response", old)

    def _body(self, r):
        self.assertIsInstance(r, Response)
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.content_type)
        self.assertIn("no-store", r.headers.get("Cache-Control", ""))
        return r.get_data(as_text=True)

    def test_first_hit_shows_the_opening_page(self):
        b = self._body(AH.open_analytics(next="/explore/?slice_id=1&x=<b>"))
        for s in ("Opening Analytics", "go=1", "next=%2Fexplore%2F%3Fslice_id%3D1%26x%3D%3Cb%3E",
                  '<noscript><meta http-equiv="refresh"'):
            self.assertIn(s, b)
        self.assertNotIn("<b>", b)

    def _fail_body(self, resolved, vouched=(None, None)):
        with mock.patch("sigzenbi.utils.resolve_bi_user", return_value=resolved), \
                mock.patch.object(AH, "_mint_with_sid", return_value=None), \
                mock.patch("sigzenbi.www.client_dashboard._vouch_for_logged_in_user", return_value=vouched):
            return self._body(AH.open_analytics(go="1"))

    def test_failure_shows_the_explained_page(self):
        b = self._fail_body((None, None))
        for s in ("open Analytics", ">Try again<", "go=1", 'href="/client_dashboard"', "mailto:support@sigzen.com"):
            self.assertIn(s, b)
        self.assertNotEqual(frappe.local.response.get("type"), "redirect")

    def test_success_still_redirects(self):
        with mock.patch("sigzenbi.utils.resolve_bi_user", return_value=("sid", "u@example.com")), \
                mock.patch.object(AH, "_mint_with_sid", return_value="https://analytics.example/h?t=x"):
            self.assertIsNone(AH.open_analytics(go="1"))
        self.assertEqual(frappe.local.response["type"], "redirect")
        self.assertEqual(frappe.local.response["location"], "https://analytics.example/h?t=x")

    def test_one_message_for_every_reason(self):
        self.assertEqual(self._fail_body((None, None)), self._fail_body(("sid", "u")))


# Owner phases P1/P2 have finished with these files (P1 complete, P2 incomplete = done touching), so none pending.
PENDING_OWNERS = set()
RENDER_MODULES = ("sigzenbi.www.databasereg.databasereg", "sigzenbi.www.db_permission.db_permission",
                  "sigzenbi.www.client_plans", "sigzenbi.www.team", "sigzenbi.www.client_dashboard",
                  "sigzenbi.www.register.register", "sigzenbi.www.thankyou", "sigzenbi.www.template_gallery")


class TestRenderErrors(FrappeTestCase):
    def test_render_errors_never_serve_raw_central_html(self):
        for name in RENDER_MODULES:
            with self.subTest(module=name):
                if name in PENDING_OWNERS:
                    continue
                tree = ast.parse(inspect.getsource(importlib.import_module(name)))
                for h in (n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)):
                    for a in (n for n in ast.walk(h) if isinstance(n, ast.Assign)):
                        self.assertFalse(isinstance(a.value, ast.Name) and a.value.id == "central_html",
                                         f"{name}:{a.lineno} serves raw Central HTML on a render error")
