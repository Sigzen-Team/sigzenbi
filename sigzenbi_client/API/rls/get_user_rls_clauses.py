"""
Computes Superset RLS WHERE clauses for a given user by leveraging
frappe.desk.reportview.build_match_conditions() — the same function ERPNext
uses for list views. "What you see in an ERPNext list = what you see in the dashboard."

No custom permission logic, no hierarchy traversal. ERPNext does it all.
"""
import frappe

# Standard ERPNext doctypes that commonly carry User Permissions (territory, branch, etc.)
_STANDARD_DOCTYPES = [
    "Sales Order", "Sales Invoice", "Delivery Note", "Customer", "Lead", "Opportunity",
    "Purchase Order", "Purchase Invoice", "Supplier",
    "Stock Entry", "Item", "Warehouse",
    "Work Order", "BOM",
    "GL Entry", "Payment Entry", "Journal Entry",
    "Employee", "Attendance", "Leave Application",
    "Project", "Task", "Timesheet",
]


def compute_rls_clauses(user_email):
    """
    Return a dict of {doctype: WHERE_clause_string} for all doctypes that have
    a User Permission active for this user.  Doctypes with no restrictions get
    an empty string (= no filter in Superset).
    """
    if not user_email or user_email == "Guest":
        return {}

    doctypes = _STANDARD_DOCTYPES
    clauses = {}

    from frappe.desk.reportview import build_match_conditions
    for doctype in doctypes:
        try:
            clause = build_match_conditions(doctype, user=user_email) or ""
        except Exception:
            clause = ""

        if clause:
            clauses[doctype] = clause

    return clauses
