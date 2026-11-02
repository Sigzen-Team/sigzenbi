"""The gateway refuses every query that names a Sigzen secret-bearing table (audit 2026-10-30 S1).

`tabSigzenBI Client Credential.api_key` / `central_api_key` are plain Data columns, and the table was
not on the refusal list, so a CTE alias trick that slipped Central's validator read it here. The
guard matches extracted identifiers, so every shape (direct, CTE, subquery, join, quoting, case)
is covered by the one list. Business tables (HR included) are NOT blocked: ERPNext permissions
govern them through the member scope."""
import unittest

from sigzenbi.API.gateway.local_db import is_read_only_sql

SECRET_TABLES = (
    "tabSigzenBI Client Credential",
)


class TestSecretTablesRefused(unittest.TestCase):
    def test_every_shape_is_refused(self):
        for t in SECRET_TABLES:
            for sql in (
                f"SELECT * FROM `{t}`",
                f"WITH x AS (SELECT * FROM `{t}`) SELECT * FROM x",
                f"WITH `{t}` AS (SELECT * FROM `{t}`) SELECT * FROM `{t}`",
                f"SELECT * FROM `tabCustomer` WHERE name IN (SELECT name FROM `{t}`)",
                f"SELECT c.name FROM `tabCustomer` c JOIN `{t}` k ON k.name = c.name",
                f"SELECT * FROM `{t.upper()}`",
                f'SELECT * FROM "{t}"',
            ):
                with self.subTest(sql=sql):
                    allowed, _ = is_read_only_sql(sql)
                    self.assertFalse(allowed, sql)

    def test_unquoted_single_word_name_is_refused(self):
        self.assertFalse(is_read_only_sql("SELECT * FROM tabSessions")[0])

    def test_business_tables_still_allowed(self):
        for t in ("tabSalary Slip", "tabEmployee", "tabSales Invoice", "tabWhatsApp Message"):
            with self.subTest(t=t):
                allowed, reason = is_read_only_sql(f"SELECT name FROM `{t}`")
                self.assertTrue(allowed, reason)


if __name__ == "__main__":
    unittest.main()
