# Copyright (c) 2026, Parin Dave and contributors
# For license information, please see license.txt

import frappe   
import requests
import json

@frappe.whitelist()
def fetch_and_update_permissions():
    """
    Fetch permissions from the external API, delete existing SigzenBI Permission Client records,
    and insert new records for the received permissions.
    """
    try:
        # Get client_name from SigzenBI Subscription Settings
        client_name = frappe.db.get_single_value('SigzenBI Subscription Settings', 'client_name')
        if client_name:
            client_name = client_name.strip()
        base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link')
        if base_url and not base_url.endswith("/"):
            base_url += "/"
        if not client_name:
            return {
                "status": "error",
                "error": "Client Name not set in Subscription Settings"
            }

        # API configuration
        API_URL = f"{base_url}api/method/sigzenbi_central.API.send_permissions.send_permissions"
        API_KEY = "3b87f054c9b1a06"
        API_SECRET = "8822a4b0438e433"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"token {API_KEY}:{API_SECRET}",
            "X-Frappe-CSRF-Token": frappe.request.cookies.get("csrf_token") or frappe.local.session.get("csrf_token")
        }
        payload = {"client_name": client_name}

        # Call the API
        frappe.log_error(title="fetch_and_update_permissions", message=f"Calling API with client_name: {client_name}")
        response = requests.post(API_URL, headers=headers, json=payload)
        response_data = response.json()
        frappe.log_error(title="fetch_and_update_permissions", message=f"API Response: {json.dumps(response_data, indent=2)}")

        if response.status_code != 200 or not (response_data.get("message") and response_data["message"].get("status") == "success"):
            return {
                "status": "error",
                "error": f"API call failed: {response_data.get('message', {}).get('error', 'Unknown error')}"
            }

        permissions = response_data["message"].get("permissions", [])
        if not permissions:
            return {
                "status": "success",
                "inserted_count": 0,
                "message": "No permissions received from API"
            }

        # Delete all existing SigzenBI Permission Client records
        frappe.db.delete('SigzenBI Permission Client', {})
        frappe.db.commit()
        frappe.log_error(title="fetch_and_update_permissions", message="Deleted all existing SigzenBI Permission Client records")

        # Insert new permissions
        inserted_count = 0
        for permission in permissions:
            try:
                doc = frappe.get_doc({
                    "doctype": "SigzenBI Permission Client",
                    "permission": permission,
                    # Add client field if mandatory, e.g.:
                    # "client": response_data["message"].get("client")
                })
                doc.insert(ignore_permissions=False)  # Respect user permissions
                inserted_count += 1
                frappe.log_error(title="fetch_and_update_permissions", message=f"Inserted permission: {permission}")
            except Exception as e:
                frappe.log_error(title="fetch_and_update_permissions", message=f"Error inserting permission {permission}: {str(e)}")
                continue  # Skip failed inserts to continue processing others

        frappe.db.commit()
        return {
            "status": "success",
            "inserted_count": inserted_count,
            "message": f"Inserted {inserted_count} permissions"
        }

    except Exception as e:
        frappe.log_error(title="fetch_and_update_permissions", message=f"Error in fetch_and_update_permissions: {str(e)}")
        return {
            "status": "error",
            "error": str(e)
        }