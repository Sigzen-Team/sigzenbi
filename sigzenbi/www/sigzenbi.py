import frappe

no_cache = 1


def get_context(context):
    """Where the Desk tile lands. The two audiences need different pages: the Workspace is hidden
    from anyone who cannot read a doctype in this module (all three are System Manager only), so
    a member sent to /desk/sigzenbi dead-ends in "No permission for Page". Navigation only --
    each destination still enforces its own auth. 302, not the default 301: the answer depends
    on who is logged in, so the browser must not cache it."""
    is_admin = "System Manager" in frappe.get_roles()
    frappe.local.flags.redirect_location = "/desk/sigzenbi" if is_admin else "/client_dashboard"
    raise frappe.Redirect(302)
