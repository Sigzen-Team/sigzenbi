"""PLAN P0.5: Central AI method paths must route to client proxies BEFORE and AFTER
Central's API/ai regroup.

Why this test matters more than it looks: the old code used str.replace() with a
hardcoded ".API.ai." key. str.replace() does not raise when its key is absent -- so
the day Central moved those modules, every rewrite would have silently become a
no-op and the browser would have started calling the Central domain directly,
violating the root CLAUDE.md rule with no error anywhere.
"""
import unittest

from sigzenbi_client.utils import route_ai_methods_to_proxy as route


class TestRouteAIMethodsToProxy(unittest.TestCase):
	def test_old_bucket_routes(self):
		"""Pre-regroup paths (API.ai.*) -- current production shape."""
		cases = [
			("sigzenbi_central.API.ai.nl2sql_api.generate_sql_from_question",
			 "sigzenbi_client.API.ai_proxy.generate_sql_from_question"),
			("sigzenbi_central.API.ai.nl2sql_api.save_chart_from_sql",
			 "sigzenbi_client.API.ai_proxy.save_chart_from_sql"),
			("sigzenbi_central.API.ai.payment_api.get_available_packs",
			 "sigzenbi_client.API.ai_proxy.get_available_packs"),
			("sigzenbi_central.API.ai.byok_api.get_ai_billing_status",
			 "sigzenbi_client.API.ai_proxy.get_ai_billing_status"),
		]
		for source, expected in cases:
			with self.subTest(source=source):
				self.assertEqual(route(source), expected)

	def test_new_buckets_route(self):
		"""Post-regroup paths. These are what Phase 0 produces."""
		cases = [
			("sigzenbi_central.API.semantic.nl2sql_api.preview_query_from_question",
			 "sigzenbi_client.API.ai_proxy.preview_query_from_question"),
			("sigzenbi_central.API.billing.payment_api.initiate_razorpay_purchase",
			 "sigzenbi_client.API.ai_proxy.initiate_razorpay_purchase"),
			("sigzenbi_central.API.billing.byok_api.save_byok_key",
			 "sigzenbi_client.API.ai_proxy.save_byok_key"),
			("sigzenbi_central.API.ai_chat.chat_api.send_message",
			 "sigzenbi_client.API.ai_proxy.send_message"),
			("sigzenbi_central.API.bi_chat.chat_dashboard.list_client_dashboards",
			 "sigzenbi_client.API.ai_proxy.list_client_dashboards"),
		]
		for source, expected in cases:
			with self.subTest(source=source):
				self.assertEqual(route(source), expected)

	def test_prefix_concatenation_form(self):
		"""ai_chat_frame.html builds two of its three paths as PREFIX + method."""
		for bucket in ("ai", "ai_chat"):
			with self.subTest(bucket=bucket):
				self.assertEqual(
					route(f"sigzenbi_central.API.{bucket}.chat_api."),
					"sigzenbi_client.API.ai_proxy.",
				)

	def test_unproxied_central_paths_untouched(self):
		"""Team and www paths have their own rewrites -- this helper must not touch them."""
		for untouched in (
			"sigzenbi_central.API.team.list_team.list_team",
			"sigzenbi_central.www.client_dashboard.renew_subscription",
			"sigzenbi_central.API.superset_sync.get_guest_token.get_superset_token",
		):
			with self.subTest(path=untouched):
				self.assertEqual(route(untouched), untouched)

	def test_realistic_html_fragment(self):
		html = """
		frappe.call({method: "sigzenbi_central.API.semantic.nl2sql_api.save_chart_from_sql"});
		frappe.call({method: "sigzenbi_central.API.team.invite_user.invite_user"});
		"""
		out = route(html)
		self.assertIn("sigzenbi_client.API.ai_proxy.save_chart_from_sql", out)
		self.assertNotIn("sigzenbi_central.API.semantic", out)
		self.assertIn("sigzenbi_central.API.team.invite_user.invite_user", out)

	def test_seat_quote_is_routed(self):
		"""P1.11: the billing page's live total. If this stays unproxied the browser calls
		the CENTRAL domain directly -- a cross-origin call from the tenant's own portal,
		which the root CLAUDE.md rule exists to prevent, and which fails on CORS anyway."""
		self.assertEqual(
			route("sigzenbi_central.API.billing.quote.quote_subscription"),
			"sigzenbi_client.API.ai_proxy.quote_subscription",
		)
		self.assertEqual(
			route("sigzenbi_central.API.billing.quote.get_rate_card"),
			"sigzenbi_client.API.ai_proxy.get_rate_card",
		)

	def test_seat_upgrade_keeps_its_own_www_rewrite(self):
		"""upgrade_subscription is a www method, not an API-bucket one: client_billing.py's
		explicit map handles it. This helper must leave it alone, or both rewrites fire."""
		path = "sigzenbi_central.www.client_dashboard.upgrade_subscription"
		self.assertEqual(route(path), path)

	def test_empty_input(self):
		self.assertEqual(route(""), "")
		self.assertIsNone(route(None))


if __name__ == "__main__":
	unittest.main()
