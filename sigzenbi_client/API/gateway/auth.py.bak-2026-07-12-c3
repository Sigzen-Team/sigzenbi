import hmac

import frappe


def _configured_client_name():
	return (
		frappe.conf.get("sigzen_client_name")
		or frappe.db.get_single_value("SigzenBI Subscription Settings", "client_name")
	)


def validate_secret(secret):
	"""Return (ok, error_message). Shared secret is required; comparison is constant-time."""
	expected = frappe.conf.get("sigzen_gateway_shared_secret")
	if not expected:
		return False, "Gateway shared secret is not configured on this site (sigzen_gateway_shared_secret in site_config.json)."

	if not secret or not hmac.compare_digest(str(secret), str(expected)):
		return False, "Invalid or missing shared secret."

	return True, None


def validate_client_name(client_name):
	"""Return (ok, error_message). Reject when a name is configured and the request does not match."""
	expected = _configured_client_name()
	if not expected:
		return True, None

	if not client_name:
		return False, "client_name is required but was not provided."

	if not hmac.compare_digest(str(client_name), str(expected)):
		return False, "client_name does not match this site's configured client name."

	return True, None


def validate_gateway_request(secret=None, client_name=None):
	ok, err = validate_secret(secret)
	if not ok:
		return False, err

	ok, err = validate_client_name(client_name)
	if not ok:
		return False, err

	return True, None
