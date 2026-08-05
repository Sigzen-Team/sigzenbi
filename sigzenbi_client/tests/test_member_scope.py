"""Trust-boundary and fail-closed tests for the member row/column scope endpoint
(`API/gateway/member_scope.get_member_scope`), the SOURCE half of Member Row Security
(SPEC-member-row-security.md §3.1, §3.5b, §3.6, §3.7 and every applicable row of §4).

Every assertion here exists because its absence is a data leak, not a cosmetic bug:

  * AuthN/AuthZ (G4) — a wrong secret or a client_name this bench does not host must be
    rejected BEFORE any permission composition runs.
  * `set_user`, not `user=` — several Frappe core hooks (Contact/Address, File, Dashboard)
    ignore the `user` parameter and read the SESSION user, and Permission Query Server Scripts
    are arbitrary customer code. Composing under the wrong session silently ignores a
    customer's own rules. Pinned by `test_composes_under_set_user_*`.
  * Every failure ends in DENY. Never "" (which means genuinely unrestricted), never a
    partially-composed clause.

The real per-tenant gateway secret is read from this site's own credentials so AuthN is
exercised for real; the tests skip if no hosted identity has one. Frappe's permission engine
is patched only where a specific SHAPE is under test (`%%`, bare ORs, PermissionError); the
child-table and permitted-field paths run against the live Frappe so they keep telling the
truth after a Frappe upgrade.
"""

import unittest
from unittest.mock import patch

import frappe

from sigzenbi_client.API.gateway import member_scope
from sigzenbi_client.API.gateway.member_scope import DENY, get_member_scope

ROSTER = "sigzenbi_client.API.gateway.poll_jobs._candidate_client_names"
BMC = "frappe.desk.reportview.build_match_conditions"
GPF = "frappe.model.get_permitted_fields"

# A real, enabled System User on this bench — the child-table and permitted-field paths are
# deliberately NOT mocked, so they need a genuine identity to compose against.
MEMBER = "avsar.kk@sigzen.com"


class MemberScopeTestCase(unittest.TestCase):
	def _creds(self):
		"""A hosted client_name that actually has a per-tenant gateway secret on this bench.
		The global shared secret was retired (auth._ACCEPT_GLOBAL_GATEWAY_SECRET = False), so
		there is no site-config shortcut here."""
		from sigzenbi_client import credentials
		from sigzenbi_client.API.gateway.poll_jobs import _candidate_client_names

		for name in _candidate_client_names():
			secret = credentials.get_gateway_secret_strict(name)
			if secret:
				return name, secret
		self.skipTest("no hosted identity on this bench has a per-tenant gateway secret")

	def _member(self):
		if not frappe.db.get_value("User", {"name": MEMBER, "enabled": 1}, "name"):
			self.skipTest(f"{MEMBER} is not an enabled user on this bench")
		return MEMBER

	def _scope(self, doctypes, member=None):
		client_name, secret = self._creds()
		res = get_member_scope(
			client_name=client_name,
			member_email=member or self._member(),
			doctypes=doctypes,
			secret=secret,
		)
		self.assertTrue(res.get("success"), res)
		return res["scope"]


class TestTrustBoundary(MemberScopeTestCase):
	"""G4: authentication and hosted-identity checks come before anything else."""

	def test_wrong_secret_is_rejected_without_composing_anything(self):
		client_name, _ = self._creds()
		with patch(BMC) as bmc, patch("frappe.set_user") as su, patch("frappe.log_error"):
			res = get_member_scope(
				client_name=client_name,
				member_email=MEMBER,
				doctypes=["Sales Invoice"],
				secret="definitely-wrong",
			)
		self.assertIs(res.get("success"), False)
		bmc.assert_not_called()
		su.assert_not_called()

	def test_missing_secret_is_rejected(self):
		client_name, _ = self._creds()
		with patch("frappe.log_error"):
			res = get_member_scope(
				client_name=client_name, member_email=MEMBER, doctypes=["Sales Invoice"], secret=None
			)
		self.assertIs(res.get("success"), False)

	def test_non_hosted_client_name_is_rejected_even_with_a_valid_secret(self):
		_, secret = self._creds()
		with patch(BMC) as bmc, patch("frappe.log_error"):
			res = get_member_scope(
				client_name="some-other-tenant",
				member_email=MEMBER,
				doctypes=["Sales Invoice"],
				secret=secret,
			)
		self.assertIs(res.get("success"), False)
		bmc.assert_not_called()


class TestB1Clause(MemberScopeTestCase):
	"""SPEC §3.1 — the clause is whatever Frappe itself would apply to this member."""

	def test_unrestricted_member_gets_an_empty_clause_not_deny(self):
		# "" and DENY are NOT interchangeable: "" means Frappe itself imposes no restriction.
		with patch(BMC, return_value=""), patch(GPF, return_value=["name"]):
			scope = self._scope(["Sales Invoice"])
		self.assertEqual(scope["Sales Invoice"]["clause"], "")

	def test_restricted_member_gets_a_parenthesised_clause(self):
		# Frappe returns bare ORs (db_query.py:677); un-parenthesised they would mis-bind when
		# AND-ed into a dataset's WHERE and silently widen the result set.
		raw = "`tabSales Invoice`.`company`='A' OR `tabSales Invoice`.`owner`='x@y.com'"
		with patch(BMC, return_value=raw), patch(GPF, return_value=["name"]):
			scope = self._scope(["Sales Invoice"])
		clause = scope["Sales Invoice"]["clause"]
		self.assertTrue(clause.startswith("(") and clause.endswith(")"), clause)
		self.assertIn("OR", clause)

	def test_percent_is_unescaped(self):
		# reportview.py:875 doubles % for its own %-formatting executor; we emit raw SQL.
		with patch(BMC, return_value="`tabSales Invoice`.`title` LIKE 'A%%'"), \
		     patch(GPF, return_value=["name"]):
			scope = self._scope(["Sales Invoice"])
		clause = scope["Sales Invoice"]["clause"]
		self.assertNotIn("%%", clause)
		self.assertIn("'A%'", clause)

	def test_no_role_read_denies_rather_than_returning_unrestricted(self):
		# frappe raises PermissionError when the member has neither read nor select nor a share.
		with patch(BMC, side_effect=frappe.PermissionError("nope")), \
		     patch(GPF, return_value=["name"]):
			scope = self._scope(["GL Entry"])
		self.assertEqual(scope["GL Entry"]["clause"], DENY)

	def test_an_unexpected_exception_denies_and_never_leaks_a_partial_clause(self):
		with patch(BMC, side_effect=RuntimeError("boom")), patch("frappe.log_error"):
			scope = self._scope(["Sales Invoice"])
		self.assertEqual(scope["Sales Invoice"]["clause"], DENY)

	def test_composes_under_set_user_and_restores_the_session(self):
		before = frappe.session.user
		seen = {}
		real_bmc = member_scope._clause_for

		def spy(doctype):
			seen["user"] = frappe.session.user
			return ""

		with patch.object(member_scope, "_clause_for", side_effect=spy), \
		     patch(GPF, return_value=["name"]):
			self._scope(["Sales Invoice"])
		self.assertEqual(seen["user"], MEMBER)  # NOT the caller's session
		self.assertEqual(frappe.session.user, before)
		self.assertIs(member_scope._clause_for, real_bmc)

	def test_session_is_restored_even_when_composition_explodes(self):
		before = frappe.session.user
		with patch(BMC, side_effect=RuntimeError("boom")), patch("frappe.log_error"):
			self._scope(["Sales Invoice"])
		self.assertEqual(frappe.session.user, before)

	def test_member_who_is_not_an_enabled_erpnext_user_denies_every_doctype(self):
		# SPEC §3.9: access follows the ERP. A deleted/disabled ERPNext user has no permissions
		# to derive, and "no permissions" must never collapse into "unrestricted".
		client_name, secret = self._creds()
		with patch(BMC) as bmc:
			res = get_member_scope(
				client_name=client_name,
				member_email="ghost@nowhere.invalid",
				doctypes=["Sales Invoice", "GL Entry"],
				secret=secret,
			)
		self.assertTrue(res["success"])
		self.assertEqual(res["scope"]["Sales Invoice"]["clause"], DENY)
		self.assertEqual(res["scope"]["GL Entry"]["clause"], DENY)
		bmc.assert_not_called()

	def test_empty_member_email_denies(self):
		client_name, secret = self._creds()
		res = get_member_scope(
			client_name=client_name, member_email="", doctypes=["Sales Invoice"], secret=secret
		)
		self.assertEqual(res["scope"]["Sales Invoice"]["clause"], DENY)

	def test_doctypes_may_arrive_as_a_json_string_over_http(self):
		with patch(BMC, return_value=""), patch(GPF, return_value=["name"]):
			scope = self._scope('["Sales Invoice", "GL Entry"]')
		self.assertEqual(sorted(scope), ["GL Entry", "Sales Invoice"])

	def test_unknown_doctype_denies(self):
		with patch("frappe.log_error"):
			scope = self._scope(["No Such Doctype At All"])
		self.assertEqual(scope["No Such Doctype At All"]["clause"], DENY)


class TestB2ChildDoctypes(MemberScopeTestCase):
	"""SPEC §3.5b — build_match_conditions RAISES for a child table, but our datasets join
	child tables, so DENY there would blank legitimate dashboards. A child row is visible iff
	its parent document is."""

	def test_child_table_uses_its_parents_restriction_not_deny(self):
		raw = "`tabSales Invoice`.`company`='Acme'"

		def bmc(doctype):
			if doctype == "Sales Invoice Item":
				raise frappe.PermissionError("child tables have no independent permission")
			return raw

		with patch(BMC, side_effect=bmc), patch(GPF, return_value=["name"]):
			scope = self._scope(["Sales Invoice Item"])
		clause = scope["Sales Invoice Item"]["clause"]
		self.assertNotEqual(clause, DENY)
		self.assertIn("tabSales Invoice", clause)
		self.assertIn("`parent`", clause)
		self.assertIn("'Acme'", clause)

	def test_child_of_an_unrestricted_parent_is_itself_unrestricted(self):
		def bmc(doctype):
			if doctype == "Sales Invoice Item":
				raise frappe.PermissionError("child")
			return ""

		with patch(BMC, side_effect=bmc), patch(GPF, return_value=["name"]):
			scope = self._scope(["Sales Invoice Item"])
		self.assertEqual(scope["Sales Invoice Item"]["clause"], "")

	def test_child_whose_parent_denies_also_denies(self):
		def bmc(doctype):
			raise frappe.PermissionError("no read anywhere")

		with patch(BMC, side_effect=bmc), patch(GPF, return_value=["name"]):
			scope = self._scope(["Sales Invoice Item"])
		self.assertEqual(scope["Sales Invoice Item"]["clause"], DENY)

	def test_child_with_an_unresolvable_parent_denies(self):
		with patch.object(member_scope, "_parent_doctype_of", return_value=None), \
		     patch(GPF, return_value=["name"]):
			scope = self._scope(["Sales Invoice Item"])
		self.assertEqual(scope["Sales Invoice Item"]["clause"], DENY)

	def test_parent_lookup_resolves_a_real_child_doctype(self):
		self.assertEqual(member_scope._parent_doctype_of("Sales Invoice Item"), "Sales Invoice")
		self.assertIsNone(member_scope._parent_doctype_of("GL Entry"))


class TestB3Flattening(MemberScopeTestCase):
	"""SPEC §3.7 — our own client guard (local_db._SENSITIVE_TABLE_RE) rejects any SQL that
	touches __Auth / tabUser* / tabSingles. A clause carrying such a subquery would be refused
	at execution, so it is resolved to literals here or the doctype denies. Business-table
	subqueries are left live."""

	def test_business_table_subquery_is_left_alone(self):
		raw = "`tabSales Invoice`.`company` IN (SELECT `name` FROM `tabCompany`)"
		self.assertEqual(member_scope._flatten_blocked_subqueries(raw), raw)

	def test_blocked_table_subquery_is_flattened_to_literals(self):
		raw = (
			"`tabSales Invoice`.`owner` IN "
			"(SELECT `name` FROM `tabUser` WHERE `name` = " + frappe.db.escape(MEMBER) + ")"
		)
		out = member_scope._flatten_blocked_subqueries(raw)
		self.assertNotEqual(out, DENY)
		self.assertNotIn("tabUser", out)
		self.assertNotIn("SELECT", out.upper())
		self.assertIn(frappe.db.escape(MEMBER), out)

	def test_a_blocked_subquery_resolving_to_nothing_matches_no_rows(self):
		raw = (
			"`tabSales Invoice`.`owner` IN "
			"(SELECT `name` FROM `tabUser` WHERE `name` = 'nobody@nowhere.invalid')"
		)
		out = member_scope._flatten_blocked_subqueries(raw)
		# `x IN (NULL)` is never true — an empty allow-list must exclude every row, not all rows.
		self.assertEqual(out, "`tabSales Invoice`.`owner` IN (NULL)")

	def test_oversized_flatten_denies_rather_than_emitting_a_monster_predicate(self):
		raw = "`tabSales Invoice`.`owner` IN (SELECT `name` FROM `tabUser`)"
		with patch.object(member_scope, "MAX_FLATTEN", 1):
			self.assertEqual(member_scope._flatten_blocked_subqueries(raw), DENY)

	def test_a_blocked_reference_that_is_not_an_in_subquery_denies(self):
		# EXISTS(...) / a correlated fragment cannot become an IN-list of literals. A visible
		# refusal beats emitting SQL our own guard will reject at execution time.
		raw = (
			"EXISTS (SELECT 1 FROM `tabUser` u WHERE u.`name` = `tabSales Invoice`.`owner`)"
		)
		self.assertEqual(member_scope._flatten_blocked_subqueries(raw), DENY)

	def test_a_correlated_blocked_subquery_denies(self):
		raw = (
			"`tabSales Invoice`.`owner` IN "
			"(SELECT `name` FROM `tabUser` WHERE `name` = `tabSales Invoice`.`owner`)"
		)
		self.assertEqual(member_scope._flatten_blocked_subqueries(raw), DENY)

	def test_flattening_denial_propagates_to_the_doctype_scope(self):
		raw = "EXISTS (SELECT 1 FROM `tabUser` u WHERE u.`name` = `tabSales Invoice`.`owner`)"
		with patch(BMC, return_value=raw), patch(GPF, return_value=["name"]):
			scope = self._scope(["Sales Invoice"])
		self.assertEqual(scope["Sales Invoice"]["clause"], DENY)

	def test_a_clause_still_touching_a_blocked_table_never_escapes(self):
		# Backstop: whatever route produced it, a clause our own guard would reject must never
		# be handed out as if it were enforceable.
		with patch.object(member_scope, "_flatten_blocked_subqueries",
		                  side_effect=lambda c: "`tabUser`.`name` = 'x'"), \
		     patch(BMC, return_value="anything"), patch(GPF, return_value=["name"]):
			scope = self._scope(["Sales Invoice"])
		self.assertEqual(scope["Sales Invoice"]["clause"], DENY)


class TestB4PermittedFields(MemberScopeTestCase):
	"""SPEC §3.6 — column security travels with the row clause."""

	def test_scope_carries_the_members_permitted_fields(self):
		with patch(BMC, return_value=""):
			scope = self._scope(["Sales Invoice"])
		fields = scope["Sales Invoice"]["fields"]
		self.assertIsInstance(fields, list)
		self.assertIn("name", fields)
		self.assertIn("company", fields)

	def test_permitted_fields_are_resolved_for_the_member_not_the_caller(self):
		with patch(BMC, return_value=""), patch(GPF, return_value=["name"]) as gpf:
			self._scope(["Sales Invoice"])
		self.assertEqual(gpf.call_args.kwargs.get("user"), MEMBER)

	def test_child_doctype_fields_are_resolved_with_its_parenttype(self):
		def bmc(doctype):
			if doctype == "Sales Invoice Item":
				raise frappe.PermissionError("child")
			return ""

		with patch(BMC, side_effect=bmc), patch(GPF, return_value=["name"]) as gpf:
			self._scope(["Sales Invoice Item"])
		self.assertEqual(gpf.call_args.kwargs.get("parenttype"), "Sales Invoice")

	def test_unresolvable_permitted_fields_deny_the_whole_doctype(self):
		with patch(BMC, return_value=""), patch(GPF, side_effect=RuntimeError("boom")), \
		     patch("frappe.log_error"):
			scope = self._scope(["Sales Invoice"])
		self.assertEqual(scope["Sales Invoice"]["clause"], DENY)

	def test_an_empty_permitted_field_list_denies(self):
		# No readable column is not "read everything" — it is nothing.
		with patch(BMC, return_value=""), patch(GPF, return_value=[]):
			scope = self._scope(["Sales Invoice"])
		self.assertEqual(scope["Sales Invoice"]["clause"], DENY)

	def test_a_denied_doctype_carries_no_field_list(self):
		with patch(BMC, side_effect=frappe.PermissionError("nope")), patch(GPF, return_value=["name"]):
			scope = self._scope(["GL Entry"])
		self.assertEqual(scope["GL Entry"], {"clause": DENY, "fields": []})


if __name__ == "__main__":
	unittest.main()
