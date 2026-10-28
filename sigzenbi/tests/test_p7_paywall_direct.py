import unittest

import frappe


class TestPaywallDirectRouteIsStyled(unittest.TestCase):
	"""P7: /paywall opened directly had no central_url, so app.css never loaded (pre-P1 page had inline styles)."""

	def test_direct_route_context_has_central_url(self):
		from sigzenbi.www import paywall

		ctx = frappe._dict()
		paywall.get_context(ctx)
		self.assertTrue(ctx.central_url and ctx.central_url.endswith("/"))

	def test_direct_render_links_the_stylesheet(self):
		from sigzenbi.www import paywall

		ctx = frappe._dict()
		paywall.get_context(ctx)
		html = frappe.render_template("sigzenbi/www/paywall.html", ctx)
		self.assertIn(ctx.central_url + "assets/sigzenbi_central/css/app.css", html)
