import frappe
import requests

@frappe.whitelist(allow_guest=True)
def submit_inquiry(name=None, email=None, message=None):
	"""
	Proxy endpoint to receive website inquiry details and forward them 
	to the central server's sigzenbi inquiry doctype API.
	"""
	if not name or not email or not message:
		frappe.throw(frappe._("Please fill out all the fields: Name, Email, and Message."))

	# 1. Retrieve the central ERP system link from Subscription Settings
	base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
	if not base_url:
		frappe.log_error(
			title="Client Proxy Inquiry Error",
			message="SigzenBI Subscription Settings does not have sigzenbi_erp_link configured."
		)
		return {
			"status": "error",
			"message": frappe._("Central server configuration is missing. Please contact the administrator.")
		}

	if not base_url.endswith('/'):
		base_url += '/'

	# 2. Build the API target URL pointing to central server's submit endpoint
	target_url = f"{base_url}api/method/sigzenbi_central.www.plans.plans.submit_inquiry"

	payload = {
		"name": name,
		"email": email,
		"message": message
	}

	try:
		# 3. Post the payload to the central server
		response = requests.post(target_url, json=payload, timeout=10)

		if response.status_code == 200:
			res_data = response.json()
			return res_data.get("message")
		else:
			frappe.log_error(
				title="Client Proxy Inquiry Error",
				message=f"Central server returned code {response.status_code}: {response.text}"
			)
			return {
				"status": "error",
				"message": frappe._("Unable to store inquiry on the central server at this time.")
			}

	except Exception as e:
		frappe.log_error(
			title="Client Proxy Inquiry Error",
			message=f"Exception while connecting to central server: {e}"
		)
		return {
			"status": "error",
			"message": frappe._("Connection to central server failed. Please try again later.")
		}


@frappe.whitelist(allow_guest=True)
def quote_subscription(plan=None, analysts=0, viewers=0, ai_licences=0,
                       interval="Month", currency="INR"):
	"""Proxy the seat-configurator price quote to Central.

	The plans page is mirrored from Central, so its JS asks for
	`/api/method/sigzenbi_central.API.billing.quote.quote_subscription` -- a CENTRAL dotted
	path, resolved against the CLIENT domain, where no such method exists. Frappe answered
	417, the page got no JSON, and every price rendered as `NaN` while the product rows
	read "Not included" beside a configurator showing 1 analyst and 2 viewers. The quote
	itself was always correct on Central (Rs 2,499 with "includes 1 analyst, 2 viewers") --
	nothing was mispriced, the number simply never arrived.

	Same shape as submit_inquiry above, and as the rewrites register.py and databasereg.py
	already do for their own endpoints. This one was the only mirrored endpoint without a
	proxy, which is why it was the only one broken.

	Pricing is public (it is the same arithmetic the /plans page shows a stranger) and
	carries no tenant data, so this is a straight pass-through with no credential.
	"""
	base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
	if not base_url:
		return {"error": frappe._("Central server is not configured.")}
	if not base_url.endswith('/'):
		base_url += '/'

	target = f"{base_url}api/method/sigzenbi_central.API.billing.quote.quote_subscription"
	params = {"analysts": analysts, "viewers": viewers, "ai_licences": ai_licences,
	          "interval": interval, "currency": currency}
	if plan:
		params["plan"] = plan

	try:
		response = requests.get(target, params=params, timeout=10)
		if response.status_code == 200:
			return response.json().get("message")
		frappe.log_error(
			title="Client Proxy Quote Error",
			message=f"Central returned {response.status_code}: {response.text[:500]}")
	except Exception as e:
		frappe.log_error(title="Client Proxy Quote Error",
		                 message=f"Exception while connecting to central server: {e}")
	# A quote that cannot be fetched must say so, not render as NaN.
	return {"error": frappe._("Could not fetch pricing right now. Please try again.")}
