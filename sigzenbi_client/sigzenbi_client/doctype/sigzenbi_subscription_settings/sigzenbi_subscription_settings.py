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

	try:
		from sigzenbi_client.utils import call_central_api
		return call_central_api(url, payload={"client_name": client_name}, method="POST", client_name=client_name)
	except Exception as e:
		frappe.log_error(title="Failed to fetch subscription details", message=frappe.get_traceback())
		frappe.throw("Failed to fetch subscription details from central server.")
