# Copyright (c) 2026, Parin Dave and contributors
# For license information, please see license.txt

import frappe
import requests
from frappe.model.document import Document

class SigzenBISubscriptionSettings(Document):
	pass

@frappe.whitelist()
def fetch_subscription_details(client_name):
	# Retrieve central ERP link dynamically from Settings
	base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
	if base_url and not base_url.endswith('/'):
		base_url += '/'
		
	if not base_url:
		frappe.throw("Central ERP Link is not set in SigzenBI Subscription Settings.")

	url = f"{base_url}api/method/sigzenbi_central.API.send_subscription_details.send_subscription_details"
	
	# Retrieve API credentials dynamically from Settings
	api_key = frappe.db.get_single_value('SigzenBI Subscription Settings', 'api_key')
	from frappe.utils.password import get_decrypted_password
	api_secret = get_decrypted_password('SigzenBI Subscription Settings', 'SigzenBI Subscription Settings', 'api_secret')
	
	try:
		response = requests.post(
			url,
			json={"client_name": client_name},
			headers={
				"Authorization": f"token {api_key}:{api_secret}",
				"Content-Type": "application/json"
			}
		)
		response.raise_for_status()
		return response.json()
	except Exception as e:
		frappe.log_error(title="Failed to fetch subscription details", message=frappe.get_traceback())
		frappe.throw("Failed to fetch subscription details from central server.")
