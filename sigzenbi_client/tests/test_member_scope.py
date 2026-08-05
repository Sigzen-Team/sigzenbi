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
# The permission engine itself. frappe.model.get_permitted_fields is deliberately NOT the seam:
# it short-circuits to every column for the 19 CORE_DOCTYPES and can never return an empty list,
# so it can neither be trusted nor tested for denial (see TestRound2AdversarialReview). The
# module asks Meta directly, so that is what the tests patch.
GPFN = "frappe.model.meta.Meta.get_permitted_fieldnames"

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
		with patch(BMC, return_value=""), patch(GPFN, return_value=["name"]):
			scope = self._scope(["Sales Invoice"])
		self.assertEqual(scope["Sales Invoice"]["clause"], "")

	def test_restricted_member_gets_a_parenthesised_clause(self):
		# Frappe returns bare ORs (db_query.py:677); un-parenthesised they would mis-bind when
		# AND-ed into a dataset's WHERE and silently widen the result set.
		raw = "`tabSales Invoice`.`company`='A' OR `tabSales Invoice`.`owner`='x@y.com'"
		with patch(BMC, return_value=raw), patch(GPFN, return_value=["name"]):
			scope = self._scope(["Sales Invoice"])
		clause = scope["Sales Invoice"]["clause"]
		self.assertTrue(clause.startswith("(") and clause.endswith(")"), clause)
		self.assertIn("OR", clause)

	def test_percent_is_unescaped(self):
		# reportview.py:875 doubles % for its own %-formatting executor; we emit raw SQL.
		with patch(BMC, return_value="`tabSales Invoice`.`title` LIKE 'A%%'"), \
		     patch(GPFN, return_value=["name"]):
			scope = self._scope(["Sales Invoice"])
		clause = scope["Sales Invoice"]["clause"]
		self.assertNotIn("%%", clause)
		self.assertIn("'A%'", clause)

	def test_no_role_read_denies_rather_than_returning_unrestricted(self):
		# frappe raises PermissionError when the member has neither read nor select nor a share.
		with patch(BMC, side_effect=frappe.PermissionError("nope")), \
		     patch(GPFN, return_value=["name"]):
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
		     patch(GPFN, return_value=["name"]):
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
		with patch(BMC, return_value=""), patch(GPFN, return_value=["name"]):
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

		with patch(BMC, side_effect=bmc), patch(GPFN, return_value=["name"]):
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

		with patch(BMC, side_effect=bmc), patch(GPFN, return_value=["name"]):
			scope = self._scope(["Sales Invoice Item"])
		self.assertEqual(scope["Sales Invoice Item"]["clause"], "")

	def test_child_whose_parent_denies_also_denies(self):
		def bmc(doctype):
			raise frappe.PermissionError("no read anywhere")

		with patch(BMC, side_effect=bmc), patch(GPFN, return_value=["name"]):
			scope = self._scope(["Sales Invoice Item"])
		self.assertEqual(scope["Sales Invoice Item"]["clause"], DENY)

	def test_child_with_an_unresolvable_parent_denies(self):
		with patch.object(member_scope, "_parent_doctypes_of", return_value=set()), \
		     patch(GPFN, return_value=["item_code"]):
			scope = self._scope(["Sales Invoice Item"])
		self.assertEqual(scope["Sales Invoice Item"]["clause"], DENY)

	def test_parent_lookup_resolves_a_real_child_doctype(self):
		self.assertEqual(member_scope._parent_doctypes_of("Sales Invoice Item"), {"Sales Invoice"})
		self.assertEqual(member_scope._parent_doctypes_of("Portal User"), {"Customer", "Supplier"})
		self.assertEqual(member_scope._parent_doctypes_of("GL Entry"), set())


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
		with patch(BMC, return_value=raw), patch(GPFN, return_value=["name"]):
			scope = self._scope(["Sales Invoice"])
		self.assertEqual(scope["Sales Invoice"]["clause"], DENY)

	def test_a_clause_still_touching_a_blocked_table_never_escapes(self):
		# Backstop: whatever route produced it, a clause our own guard would reject must never
		# be handed out as if it were enforceable.
		with patch.object(member_scope, "_flatten_blocked_subqueries",
		                  side_effect=lambda c: "`tabUser`.`name` = 'x'"), \
		     patch(BMC, return_value="anything"), patch(GPFN, return_value=["name"]):
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
		with patch(BMC, return_value=""), patch(GPFN, return_value=["name"]) as gpfn:
			self._scope(["Sales Invoice"])
		self.assertEqual(gpfn.call_args.kwargs.get("user"), MEMBER)

	def test_child_doctype_fields_are_resolved_with_its_parenttype(self):
		# A child's field permissions come from its PARENT's perms (Meta.get_permissions), and
		# without a parenttype Frappe returns [] for every child table.
		def bmc(doctype):
			if doctype == "Sales Invoice Item":
				raise frappe.PermissionError("child")
			return ""

		with patch(BMC, side_effect=bmc), patch(GPFN, return_value=["item_code"]) as gpfn:
			self._scope(["Sales Invoice Item"])
		self.assertEqual(gpfn.call_args.kwargs.get("parenttype"), "Sales Invoice")

	def test_unresolvable_permitted_fields_deny_the_whole_doctype(self):
		with patch(BMC, return_value=""), patch(GPFN, side_effect=RuntimeError("boom")), \
		     patch("frappe.log_error"):
			scope = self._scope(["Sales Invoice"])
		self.assertEqual(scope["Sales Invoice"]["clause"], DENY)

	def test_an_empty_permitted_field_list_denies(self):
		# No readable column is not "read everything" — it is nothing. Unlike the first round,
		# this guard is now reachable in production: see TestRound2AdversarialReview.
		with patch(BMC, return_value=""), patch(GPFN, return_value=[]):
			scope = self._scope(["Sales Invoice"])
		self.assertEqual(scope["Sales Invoice"]["clause"], DENY)

	def test_a_denied_doctype_carries_no_field_list(self):
		with patch(BMC, side_effect=frappe.PermissionError("nope")), patch(GPFN, return_value=["name"]):
			scope = self._scope(["GL Entry"])
		self.assertEqual(scope["GL Entry"], {"clause": DENY, "fields": []})


class TestRound2AdversarialReview(MemberScopeTestCase):
	"""Every test here reproduces a finding from the round-2 adversarial review and fails on the
	code as first shipped. They are grouped rather than scattered so the next reviewer can see at
	a glance what was actually proven, not merely claimed."""

	def _roleless_member(self):
		"""An enabled ERPNext user holding NO role. The permission engine grants it nothing, so
		every axis must deny — which is exactly what the column axis did NOT do."""
		email = "scope-noroles@sigzen.invalid"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "No Roles",
					"user_type": "System User",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)
		frappe.db.set_value("User", email, "enabled", 1)
		frappe.db.delete("Has Role", {"parent": email})
		frappe.db.commit()
		# addCleanup, not tearDown: unittest skips tearDown when setUp raises.
		self.addCleanup(self._drop_member, email)
		frappe.clear_cache(user=email)
		return email

	def _drop_member(self, email):
		frappe.set_user("Administrator")
		frappe.delete_doc("User", email, force=True, ignore_permissions=True, delete_permanently=True)
		frappe.db.commit()

	# ---- column axis (SPEC §3.6) ----

	def test_a_core_doctype_the_member_has_no_role_on_denies(self):
		# frappe.model.get_permitted_fields returns EVERY column, with NO user check at all, for
		# the 19 CORE_DOCTYPES (frappe/model/__init__.py:228). Client Script grants read only to
		# System Manager/Administrator, so a role-less member must get nothing.
		member = self._roleless_member()
		from frappe.model import get_permitted_fields

		self.assertTrue(get_permitted_fields("Client Script", user=member))  # the fail-open
		with patch(BMC, return_value=""), patch("frappe.log_error"):
			scope = self._scope(["Client Script"], member=member)
		self.assertEqual(scope["Client Script"], {"clause": DENY, "fields": []})

	def test_core_doctype_columns_the_member_may_not_read_are_withheld(self):
		# tabUser grants role "All" read on its own row (if_owner), so this member is NOT denied
		# outright — which is exactly where the missing user check leaked: get_permitted_fields
		# hands back the permlevel-1 columns (api_key, api_secret, reset_password_key) too.
		member = self._roleless_member()
		from frappe.model import get_permitted_fields

		wide_open = get_permitted_fields("User", user=member)
		self.assertIn("api_secret", wide_open)  # the fail-open, stated as a fact

		with patch(BMC, return_value=""), patch("frappe.log_error"):
			scope = self._scope(["User"], member=member)
		fields = scope["User"]["fields"]
		self.assertIn("email", fields)          # permlevel 0 — legitimately readable
		self.assertNotIn("api_key", fields)
		self.assertNotIn("api_secret", fields)
		self.assertNotIn("reset_password_key", fields)

	def test_a_member_with_no_readable_field_denies_on_an_ordinary_doctype(self):
		# The "empty field list denies" guard was unreachable: get_permitted_fields always
		# returns at least default+optional fields (frappe/model/__init__.py:254).
		member = self._roleless_member()
		with patch(BMC, return_value=""), patch("frappe.log_error"):
			scope = self._scope(["Sales Invoice"], member=member)
		self.assertEqual(scope["Sales Invoice"], {"clause": DENY, "fields": []})

	def test_a_permitted_member_still_gets_a_real_field_list(self):
		# The fix must not become a blanket deny: a member with roles keeps its columns.
		with patch(BMC, return_value=""):
			scope = self._scope(["Sales Invoice"])
		fields = scope["Sales Invoice"]["fields"]
		self.assertIn("name", fields)
		self.assertIn("company", fields)
		self.assertIn("creation", fields)

	# ---- malformed request (fail-closed contract) ----

	def test_a_malformed_doctypes_payload_is_a_failure_not_an_empty_success(self):
		# An empty `scope` map with success=True is a cacheable answer carrying no DENY row for
		# a doctype that WAS requested — the ""/DENY conflation this feature exists to remove.
		client_name, secret = self._creds()
		for payload in ({"Sales Invoice": 1}, 17, ["Sales Invoice", 99], [""], [], None):
			with self.subTest(payload=payload), patch("frappe.log_error"):
				res = get_member_scope(
					client_name=client_name,
					member_email=MEMBER,
					doctypes=payload,
					secret=secret,
				)
				self.assertIs(res.get("success"), False, res)

	# ---- flattening (SPEC §3.7) ----

	def test_a_subquery_that_is_not_a_plain_select_is_refused_without_executing(self):
		# A Permission Query Server Script's output is customer-authored text that reaches this
		# function verbatim. `;` alone is not a statement-shape guard.
		for fragment in (
			"SELECT `name` FROM `tabUser` INTO OUTFILE '/tmp/sigzen-scope-probe'",
			"UPDATE `tabUser` SET `enabled` = 0",
			"SELECT /*!50000 `name` */ FROM `tabUser`",
			"DELETE FROM `tabUser`",
			"(SELECT 1) UNION SELECT `api_secret` FROM `tabUser`; SELECT 1",
		):
			with self.subTest(fragment=fragment), patch("frappe.db.sql") as sql:
				self.assertEqual(member_scope._literals_for(fragment), DENY)
				sql.assert_not_called()

	def test_a_parenthesis_inside_a_string_literal_does_not_missplit_the_subquery(self):
		# _matching_paren ignored quoting, so a ')' in a literal closed the subquery early and
		# produced a fragment that neither flattens nor denies for the right reason.
		raw = (
			"`tabSales Invoice`.`owner` IN (SELECT `name` FROM `tabUser` "
			"WHERE `full_name` != 'a)b' AND `name` = " + frappe.db.escape(MEMBER) + ")"
		)
		with patch("frappe.log_error"):
			out = member_scope._flatten_blocked_subqueries(raw)
		self.assertNotEqual(out, DENY)
		self.assertNotIn("tabUser", out)
		self.assertIn(frappe.db.escape(MEMBER), out)

	# ---- child doctypes (SPEC §3.5b) ----

	def test_a_child_used_by_several_parents_is_scoped_per_parenttype_not_denied(self):
		# 55 of this bench's 413 child doctypes have more than one parent (verified live), and
		# every one of them denied — blanking datasets for members with zero restrictions.
		def bmc(doctype):
			if doctype == "Customer":
				return "`tabCustomer`.`name` = 'C-1'"
			if doctype == "Supplier":
				return ""
			raise frappe.PermissionError("child tables have no independent permission")

		with patch(BMC, side_effect=bmc), patch(GPFN, return_value=["user"]):
			scope = self._scope(["Portal User"])
		clause = scope["Portal User"]["clause"]
		self.assertNotEqual(clause, DENY)
		self.assertIn("`tabPortal User`.`parenttype` = 'Customer'", clause)
		self.assertIn("`tabPortal User`.`parent` IN (SELECT `name` FROM `tabCustomer`", clause)
		self.assertIn("`tabPortal User`.`parenttype` = 'Supplier'", clause)
		self.assertIn("'C-1'", clause)

	def test_a_parent_the_member_cannot_read_contributes_no_arm(self):
		def bmc(doctype):
			if doctype == "Supplier":
				return ""
			raise frappe.PermissionError("no read")

		with patch(BMC, side_effect=bmc), patch(GPFN, return_value=["user"]):
			scope = self._scope(["Portal User"])
		clause = scope["Portal User"]["clause"]
		self.assertIn("`tabPortal User`.`parenttype` = 'Supplier'", clause)
		self.assertNotIn("Customer", clause)

	def test_a_child_whose_every_parent_denies_denies(self):
		with patch(BMC, side_effect=frappe.PermissionError("no read anywhere")), \
		     patch(GPFN, return_value=["user"]):
			scope = self._scope(["Portal User"])
		self.assertEqual(scope["Portal User"]["clause"], DENY)

	def test_the_child_clause_qualifies_its_own_columns(self):
		# A bare `parent` is ambiguous as soon as two child tables appear in one dataset (MySQL
		# 1052) and gives the Stage C alias rewriter nothing to anchor on.
		def bmc(doctype):
			if doctype == "Sales Invoice":
				return "`tabSales Invoice`.`company` = 'Acme'"
			raise frappe.PermissionError("child")

		with patch(BMC, side_effect=bmc), patch(GPFN, return_value=["item_code"]):
			scope = self._scope(["Sales Invoice Item"])
		clause = scope["Sales Invoice Item"]["clause"]
		self.assertIn("`tabSales Invoice Item`.`parent` IN", clause)
		self.assertNotIn("(`parent` IN", clause)

	def test_child_fields_are_the_intersection_across_its_possible_parents(self):
		# A column readable only under ONE parent must not become readable on rows belonging to
		# another — the row set spans both parenttypes.
		def fieldnames(*_a, parenttype=None, **_kw):
			return ["user"] if parenttype == "Customer" else ["user", "supplier_only_note"]

		with patch(BMC, return_value=""), patch(GPFN, side_effect=fieldnames):
			scope = self._scope(["Portal User"])
		self.assertIn("user", scope["Portal User"]["fields"])
		self.assertNotIn("supplier_only_note", scope["Portal User"]["fields"])

	def test_a_child_with_no_resolvable_parent_denies(self):
		# 17 child doctypes on this bench are referenced by no parent at all. SPEC §4.
		with patch(GPFN, return_value=["user"]), patch("frappe.log_error"):
			scope = self._scope(["Log Setting User"])
		self.assertEqual(scope["Log Setting User"]["clause"], DENY)

	# ---- request-local state (the module docstring's own claim) ----

	def test_the_request_form_dict_and_session_data_survive_the_impersonation(self):
		# frappe.set_user (frappe/__init__.py:367-380) wipes local.form_dict, session.data,
		# session.sid and local.cache; set_user(caller) does NOT put any of them back.
		before_form_dict = frappe.local.form_dict
		self.addCleanup(setattr, frappe.local, "form_dict", before_form_dict)
		frappe.local.form_dict = frappe._dict({"cmd": "sigzen-test-marker"})
		frappe.local.session.data["sigzen_test_marker"] = 1
		sid = frappe.local.session.sid

		with patch(BMC, return_value=""), patch(GPFN, return_value=["name"]):
			self._scope(["Sales Invoice"])

		self.assertEqual(frappe.local.form_dict.get("cmd"), "sigzen-test-marker")
		self.assertEqual(frappe.local.session.data.get("sigzen_test_marker"), 1)
		self.assertEqual(frappe.local.session.sid, sid)

	# ---- disclosure ----

	def test_auth_failure_does_not_reveal_whether_a_client_has_a_secret(self):
		# validate_secret's two distinct messages let an unauthenticated caller enumerate which
		# client_names are provisioned on this bench.
		client_name, _ = self._creds()
		with patch("frappe.log_error"):
			configured = get_member_scope(
				client_name=client_name, member_email=MEMBER,
				doctypes=["Sales Invoice"], secret="definitely-wrong",
			)
			unknown = get_member_scope(
				client_name="no-such-tenant-anywhere", member_email=MEMBER,
				doctypes=["Sales Invoice"], secret="definitely-wrong",
			)
		self.assertIs(configured.get("success"), False)
		self.assertIs(unknown.get("success"), False)
		self.assertEqual(configured.get("message"), unknown.get("message"))


class TestLiveComposition(MemberScopeTestCase):
	"""SPEC §5 asked for clause composition proven against LIVE Frappe, per mechanism. The first
	round mocked build_match_conditions in 10 of 11 clause tests, so nothing automated proved the
	one claim the whole module exists for: that a rule we cannot see from Central is honoured."""

	def _restrict(self, member, doctype, docname):
		"""A real User Permission — the mechanism Frappe folds into build_match_conditions.
		Reuses one that already exists (and then leaves it alone): a test that fails because the
		bench already carries the state it wanted is a test nobody trusts."""
		existing = frappe.db.get_value(
			"User Permission", {"user": member, "allow": doctype, "for_value": docname}, "name"
		)
		if existing:
			return existing
		perm = frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": member,
				"allow": doctype,
				"for_value": docname,
				"apply_to_all_doctypes": 1,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		# addCleanup, not tearDown: unittest skips tearDown when setUp raises.
		self.addCleanup(self._unrestrict, perm.name, member)
		frappe.clear_cache(user=member)
		return perm.name

	def _unrestrict(self, name, member):
		frappe.set_user("Administrator")
		frappe.delete_doc("User Permission", name, force=True, ignore_permissions=True)
		frappe.db.commit()
		frappe.clear_cache(user=member)

	def test_a_live_user_permission_lands_in_the_clause(self):
		company = frappe.db.get_value("Company", {}, "name")
		if not company:
			self.skipTest("no Company on this bench")
		self._restrict(self._member(), "Company", company)
		scope = self._scope(["Sales Invoice"])
		clause = scope["Sales Invoice"]["clause"]
		self.assertNotEqual(clause, DENY)
		self.assertIn(frappe.db.escape(company), clause)
		self.assertIn("`company`", clause)

	def test_a_live_permission_query_conditions_hook_is_honoured(self):
		# THE claim: a rule that exists only in the tenant's own code — invisible to Central —
		# still reaches the clause, because composition happens here, under the member's session.
		marker = "`tabSales Invoice`.`title` = 'sigzen-hook-marker'"
		hooks = frappe.get_hooks()

		def fake_get_hooks(hook=None, *args, **kwargs):
			if hook == "permission_query_conditions":
				return {"Sales Invoice": ["sigzenbi_client.tests.test_member_scope._hook_condition"]}
			return hooks.get(hook) if hook else hooks

		with patch("frappe.get_hooks", side_effect=fake_get_hooks):
			scope = self._scope(["Sales Invoice"])
		self.assertIn(marker, scope["Sales Invoice"]["clause"])

	def test_a_live_child_table_composes_from_its_real_parent(self):
		scope = self._scope(["Sales Invoice Item"])
		clause = scope["Sales Invoice Item"]["clause"]
		self.assertNotEqual(clause, DENY)
		self.assertIn("item_code", scope["Sales Invoice Item"]["fields"])


def _hook_condition(user=None, doctype=None):
	"""Stand-in for a customer app's permission_query_conditions hook (see the test above)."""
	return "`tabSales Invoice`.`title` = 'sigzen-hook-marker'"


if __name__ == "__main__":
	unittest.main()
