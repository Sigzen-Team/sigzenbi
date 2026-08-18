"""The gateway read-only guard vs string LITERALS (live false-reject, 2026-08-06).

`customer IN ('Grant Plastics Ltd.')` is data, not a GRANT statement — the member-RLS
clause injection made such literals routine, and the guard refused every query for a member
scoped to that customer. These tests pin: literals are inert to every statement-level check,
while real write statements — including ones a literal tries to hide behind — stay blocked.
"""
import unittest

from sigzenbi_client.API.gateway.local_db import is_read_only_sql


def ok(sql):
    allowed, reason = is_read_only_sql(sql)
    return allowed, reason


class TestLiteralsAreInert(unittest.TestCase):
    def test_keyword_inside_a_literal_is_allowed(self):
        for sql in (
            "SELECT 1 FROM `tabSales Invoice` WHERE `customer` IN ('Grant Plastics Ltd.')",
            "SELECT 1 FROM `tabSales Invoice` WHERE note = 'please CALL and UPDATE me'",
            "SELECT 'DROP TABLE x' AS label FROM DUAL",
            "SELECT 1 FROM `tabItem` WHERE name = 'Replace-A--B'",
        ):
            with self.subTest(sql):
                allowed, reason = ok(sql)
                self.assertTrue(allowed, reason)

    def test_semicolon_inside_a_literal_is_allowed(self):
        allowed, reason = ok("SELECT 1 FROM `tabCustomer` WHERE name = 'O''Brien; Sons'")
        self.assertTrue(allowed, reason)

    def test_sensitive_table_name_as_a_literal_value_is_allowed(self):
        allowed, reason = ok("SELECT 1 FROM `tabDocField` WHERE parent = 'tabUser'")
        self.assertTrue(allowed, reason)

    def test_escaped_quotes_do_not_open_a_hole(self):
        # A backslash-escaped quote must not end the literal early and hide what follows.
        allowed, _ = ok("SELECT 1 FROM `tabX` WHERE a = 'p\\'; DROP TABLE `tabX`; --'")
        # Whole thing is ONE literal -> inert -> allowed as a read.
        self.assertTrue(allowed)

    def test_a_quote_inside_a_comment_cannot_fake_a_literal(self):
        # Adversarial: /* ' */ ; DROP ... /* ' */ — naive literal-stripping would read the
        # region between the two comment-quotes as one literal and blank the DROP.
        allowed, _ = ok("SELECT 1 /* ' */ ; DROP TABLE x /* ' */")
        self.assertFalse(allowed)

    def test_real_write_statements_stay_blocked(self):
        for sql in (
            "DROP TABLE `tabSales Invoice`",
            "SELECT 1; DROP TABLE `tabX`",
            "SELECT 1 FROM `tabX` WHERE a = 'v'; DELETE FROM `tabX`",
            "INSERT INTO `tabX` VALUES (1)",
            "SELECT * FROM `tabUser`",
            "SELECT 1 INTO OUTFILE '/tmp/x' FROM `tabX`",
            "SELECT /*! UNION SELECT password FROM __Auth */ 1",
        ):
            with self.subTest(sql):
                allowed, _ = ok(sql)
                self.assertFalse(allowed, sql)


if __name__ == "__main__":
    unittest.main()
