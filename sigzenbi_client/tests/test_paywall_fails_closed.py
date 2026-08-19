""": the dashboard paywall fails CLOSED, and distinguishes "unknown".

It used to fail OPEN: any Central lookup error left state=None and fell through to the
normal render, so an expired tenant saw their dashboards whenever Central was slow or
unreachable. A gate that fails open is not a gate.

Failing straight to the paywall would be the opposite error -- telling a PAYING customer
they have not paid because of a transient blip. So there are three states, and the third
is the point:

    entitled     -> dashboards
    not entitled -> paywall (shown-and-paywalled, SPEC 7)
    unknown      -> neither; a "temporarily unavailable" screen

The paywall is UX only. Central refuses the endpoints regardless (P23.8), so nothing
here grants anything.
"""
import inspect
import unittest

from sigzenbi_client.www import client_dashboard


class TestFailsClosed(unittest.TestCase):
	def test_lookup_failure_does_not_render_dashboards(self):
		src = inspect.getsource(client_dashboard.get_context)
		self.assertIn("if state is None:", src,
		              "a Central lookup failure must not fall through to the dashboards")
		# The unknown branch must RETURN, not fall through. Slice to the next
		# top-level statement rather than to a blank line -- the branch contains one.
		branch = src[src.index("if state is None:"):]
		branch = branch[:branch.index("\n    #")]
		self.assertIn("return context", branch)

	def test_unknown_is_distinct_from_not_entitled(self):
		"""Failing to the paywall on a blip accuses a paying customer of not paying."""
		src = inspect.getsource(client_dashboard.get_context)
		self.assertIn("entitlement_unknown", src)

	def test_unknown_branch_precedes_the_entitlement_check(self):
		src = inspect.getsource(client_dashboard.get_context)
		self.assertLess(src.index("if state is None:"), src.index('state.get("status") == "Expired"'))


class TestPerFeatureGating(unittest.TestCase):
	def test_bi_entitlement_is_checked_not_just_liveness(self):
		"""An AI-only tenant has an ACTIVE subscription and must still not see
		dashboards they did not buy."""
		src = inspect.getsource(client_dashboard.get_context)
		self.assertIn('state.get("bi"', src)

	def test_bi_defaults_to_true_when_the_key_is_absent(self):
		"""Central may be a release behind and not send the key yet. Defaulting to
		False would paywall every tenant during that window."""
		src = inspect.getsource(client_dashboard.get_context)
		self.assertIn('state.get("bi", True)', src)


class TestItGrantsNothing(unittest.TestCase):
	def test_the_paywall_branch_only_renders(self):
		src = inspect.getsource(client_dashboard.get_context)
		branch = src[src.index('state.get("status") == "Expired"'):]
		branch = branch[:branch.index("return context")]
		for forbidden in ("set_value", ".save(", "delete_doc", "insert("):
			with self.subTest(call=forbidden):
				self.assertNotIn(forbidden, branch)
