# Copyright (c) 2026, Parin Dave and contributors
# For license information, please see license.txt

import frappe
import requests
from frappe.model.document import Document

class SigzenBISubscriptionSettings(Document):
	pass

@frappe.whitelist()
def fetch_subscription_details(client_name):
	url = "http://192.168.1.12:8003/api/method/sigzenbi_central.API.send_subscription_details.send_subscription_details"
	api_key = "2444eb73c70d250"
	api_secret = "892b6a6f7860ceb"
	
	try:
		response = requests.post(
			url,
			json={"client_name": client_name},
			headers={
				"Authorization": f"token {api_key}:{api_secret}",
				"Content-Type": "application/json",
				"Host": "sigzenbi_central"
			}
		)
		response.raise_for_status()
		return response.json()
	except Exception as e:
		frappe.log_error(title="Failed to fetch subscription details", message=frappe.get_traceback())
		frappe.throw("Failed to fetch subscription details from central server.")
