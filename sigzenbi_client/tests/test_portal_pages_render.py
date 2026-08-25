"""Every portal page must BUILD ITS CONTEXT for a signed-in tenant.

WHY THIS EXISTS (regression, 2026-08-16). Fields were removed from
`SigzenBI Subscription Settings` while six pages still called
`frappe.db.get_single_value(...)` on them. That call RAISES on a removed field -- it does not
return None -- so every logged-in page returned `Server Error 417: There was an error building
this page`.

It shipped because the smoke test curled the pages as a GUEST. A guest is redirected to
/portal/login before `get_context` runs, so the harness collected a row of 200s while the page
code never executed once. The green was an artifact of the precondition.

So this test does the one thing that check could not: it gets PAST the auth gate and runs the
real `get_context`. Everything after `resolve_bi_user()` -- which is where the whole page body
lives -- is then actually executed.

WHAT IT CANNOT SEE: template rendering (a broken Jinja block still passes here), anything
client-side, and anything that only fires on a real request object. It is the cheap 80%, not a
browser test. Pair it with one logged-in browser pass on the pages you touched.
"""

import importlib
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

# Every page a signed-in tenant can land on. client_login/proxy/thankyou are excluded on
# purpose: they are pre-auth or redirect-only surfaces with no tenant context to build.
PAGES = [
    "client_dashboard",
    "client_billing",
    "team",
    "client_plans",
    "ai_chat",
    "bi_chat",
    "ai_chart",
    "template_gallery",
    # client_home was deleted 2026-08-16: nothing linked to it and Website Settings.home_page
    # is "login", so it was unreachable.
]


class TestPortalPagesRenderForASignedInTenant(FrappeTestCase):
    def setUp(self):
        row = frappe.db.get_value("SigzenBI Users", {}, ["user_id"], as_dict=True)
        self.tenant_user = (row and row.user_id) or "Administrator"
        # A real request carries a session object; the console/test runner does not, and
        # several pages mint a CSRF token. Stub just that, so the rest runs untouched.
        self._patches = [
            patch("frappe.sessions.get_csrf_token", return_value="test-csrf"),
            patch("sigzenbi_client.utils.resolve_bi_user",
                  return_value=("test-central-sid", self.tenant_user)),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def test_every_page_builds_its_context(self):
        """The check that would have caught the 417."""
        failures = []
        for page in PAGES:
            mod_name = "sigzenbi_client.www.%s" % page
            try:
                mod = importlib.import_module(mod_name)
            except Exception as exc:
                failures.append("%s: IMPORT %s: %s" % (page, type(exc).__name__, exc))
                continue
            if not hasattr(mod, "get_context"):
                continue
            try:
                mod.get_context(frappe._dict())
            except frappe.Redirect:
                # A page choosing to redirect is a decision, not a crash.
                pass
            except Exception as exc:
                failures.append("%s: %s: %s" % (page, type(exc).__name__, str(exc)[:160]))

        self.assertEqual(
            failures, [],
            "these pages raise while building their context -- a signed-in tenant would get "
            "'Server Error 417':\n  " + "\n  ".join(failures))

    def test_mirror_pages_render_real_central_html_not_the_fallback(self):
        """A page that FAILS OPEN must not read as a pass.

        The mirror pages fetch their markup from Central and, if that fetch fails, substitute
        `utils.guided_fallback` -- "... is temporarily unavailable". That is correct behaviour
        for a customer, and it is exactly why "get_context did not raise" is too weak a bar: a
        totally broken Central contract renders a friendly page and the test goes green.

        So assert the real thing arrived. Skips rather than fails when Central is genuinely
        unreachable from the test box -- an infrastructure outage is not a code regression, and
        a test that cries wolf gets muted.
        """
        import requests

        base = frappe.db.get_single_value("SigzenBI Subscription Settings", "sigzenbi_erp_link")
        if not base:
            self.skipTest("no Central URL configured on this site")
        try:
            requests.get(base.rstrip("/") + "/api/method/ping", timeout=10)
        except Exception:
            self.skipTest("Central unreachable from this box -- infra, not a code regression")

        MIRRORS = ["client_billing", "team", "client_plans", "template_gallery"]
        weak = []
        for page in MIRRORS:
            mod = importlib.import_module("sigzenbi_client.www.%s" % page)
            ctx = frappe._dict()
            try:
                mod.get_context(ctx)
            except frappe.Redirect:
                continue
            html = ctx.get("central_html") or ctx.get("html_content") or ""
            if html and "temporarily unavailable" in html:
                weak.append("%s: rendered the guided_fallback, not Central's markup" % page)
        self.assertEqual(
            weak, [],
            "these pages fell back instead of rendering Central's real HTML -- the page still "
            "'works' for a customer, which is why this needs its own assertion:\n  "
            + "\n  ".join(weak))

    def test_no_page_reads_a_field_that_no_longer_exists(self):
        """Catches the specific shape directly, so the failure NAMES the field.

        `get_single_value` on a removed field raises, and that is exactly how the outage
        happened. Rather than trusting every page to be exercised above, assert that every
        Subscription Settings field the www/ layer asks for still exists on the doctype.
        """
        import os
        import re

        app = frappe.get_app_path("sigzenbi_client")
        meta_fields = {d.fieldname for d in frappe.get_meta("SigzenBI Subscription Settings").fields}
        pattern = re.compile(
            r"""get_single_value\(\s*["']SigzenBI Subscription Settings["']\s*,\s*["'](\w+)["']""",
            re.S)
        missing = []
        for root, _dirs, files in os.walk(os.path.join(app, "www")):
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                path = os.path.join(root, fname)
                with open(path, encoding="utf-8") as fh:
                    src = fh.read()
                for field in pattern.findall(src):
                    if field not in meta_fields:
                        missing.append("%s -> %s" % (os.path.relpath(path, app), field))

        self.assertEqual(
            missing, [],
            "these read a SigzenBI Subscription Settings field that has been REMOVED; "
            "get_single_value raises on a missing field, so each one is a 417 waiting to "
            "happen:\n  " + "\n  ".join(missing))
