import frappe

no_cache = 1


def get_context(context):
	# Direct /paywall: callers that render paywall.html pass their own central_url; this route had none,
	# so app.css never loaded and the page showed unstyled (P7 finding).
	base = frappe.db.get_single_value("SigzenBI Subscription Settings", "sigzenbi_erp_link") or ""
	context.central_url = base if not base or base.endswith("/") else base + "/"
