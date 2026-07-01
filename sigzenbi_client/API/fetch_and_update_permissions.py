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
        headers = {}
        csrf_token = (frappe.request.cookies.get("csrf_token") if getattr(frappe.local, "request", None) else None) or frappe.local.session.get("csrf_token")
        if csrf_token:
            headers["X-Frappe-CSRF-Token"] = csrf_token
        payload = {"client_name": client_name}

        # Call the API
        frappe.log_error(title="fetch_and_update_permissions", message=f"Calling API with client_name: {client_name}")
        from sigzenbi_client.utils import call_central_api
        response_data = call_central_api(API_URL, payload=payload, method="POST", headers=headers)
        frappe.log_error(title="fetch_and_update_permissions", message=f"API Response: {json.dumps(response_data, indent=2)}")

        if not response_data or response_data.get("status") != "success":
            return {
                "status": "error",
                "error": f"API call failed: {response_data.get('error', 'Unknown error') if isinstance(response_data, dict) else 'Unknown error'}"
            }

        permissions = response_data.get("permissions", [])
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