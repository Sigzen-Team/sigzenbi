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
