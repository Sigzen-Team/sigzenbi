"""The analytics hand-off must survive a stale `central_sid` (founder, 2026-08-09:
"SSO to superset is not working sometimes").

A `central_sid` cookie OUTLIVES the Central session it names — expiry, a logout, or anything that
drops the session leaves the cookie in the browser looking perfectly good. The portal PAGE
survives that because `central_get_with_sid` re-vouches once on 401/403. `open_analytics` used
bare `team_proxy._forward`, which does not, so it inherited exactly the bug that helper exists to
fix.

That is why it read as random rather than broken: the dashboard keeps rendering (it re-vouched),
so the customer is certain they are signed in, and ONLY "Open Analytics" bounces them back to the
dashboard. Reproduced by killing the Central session and clicking — dashboard renders, hand-off
does not reach Superset.
"""
import ast
import inspect
import textwrap
import unittest

from frappe.tests.utils import FrappeTestCase

from sigzenbi_client.API import analytics_handoff as AH


def _strip(source):
    """Executable code only: no comments AND no docstrings.

    Stripping `#` lines is not enough — this module's prose names `call_central_api` and
    `_forward` precisely to explain why they are NOT used, and a naive scan reads that
    explanation as the violation. (It did, on the first run of this test.)
    """
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(body, list) and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _code():
    return _strip(inspect.getsource(AH.open_analytics))


class TestHandoffRecoversAStaleSession(FrappeTestCase):
    def test_it_re_vouches_when_the_mint_fails(self):
        self.assertIn("_vouch_for_logged_in_user", _code(),
                      "the hand-off no longer recovers a stale central_sid — this is the "
                      "'works sometimes' bug")

    def test_it_retries_the_mint_with_the_new_sid(self):
        code = _code()
        vouch_at = code.index("_vouch_for_logged_in_user")
        self.assertIn("_mint_with_sid", code[vouch_at:],
                      "it re-vouches but never retries, so the recovery is dead code")

    def test_it_never_authenticates_with_the_tenant_api_key(self):
        """`call_central_api` sends the tenant api_key, which authenticates as the ORG OWNER.
        On a login endpoint that would sign every member in as the owner."""
        code = _strip(inspect.getsource(AH))
        self.assertNotIn("call_central_api", code)
        self.assertNotIn("Authorization", code)

    def test_identity_comes_from_the_resolver_and_NOT_from_the_cookie(self):
        """A SECURITY assertion, not a freshness one.

        This endpoint used to read `central_sid` straight off the request, which reintroduced the
        stale-cookie identity bleed `resolve_bi_user` exists to prevent — on the one endpoint that
        grants a LOGIN. Measured before the fix: sign in as dixit (analyst), switch the ERP
        session to sales1 (a VIEWER), click Open Analytics -> signed into Superset as dixit.f.
        A viewer inheriting an analyst's analytics session out of a cookie.
        """
        code = _code()
        self.assertIn("resolve_bi_user", code,
                      "identity is not resolved live — the cookie can name a different person "
                      "than the live session, and this endpoint hands out a login")
        self.assertNotIn('cookies.get("central_sid")', code,
                         "the sid is being read from the cookie again")

    def test_nothing_is_minted_without_a_resolved_member(self):
        """`resolve_bi_user` returns (None, None) when the live ERP user is not a vouchable
        member. That must stop the mint, not fall through to the cookie's sid."""
        code = _code()
        mint_at = code.index("_mint_with_sid")
        self.assertIn("client_user", code[:mint_at],
                      "the mint runs before the resolver's answer is checked")

    def test_a_failure_still_lands_the_customer_in_the_portal(self):
        code = _code()
        self.assertIn("/client_dashboard", code)
        self.assertIn("redirect", code)
