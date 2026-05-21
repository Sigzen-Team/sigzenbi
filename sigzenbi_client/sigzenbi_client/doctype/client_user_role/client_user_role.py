# Copyright (c) 2026, Parin Dave and contributors
# For license information, please see license.txt

# pyrefly: ignore [missing-import]
import frappe
from frappe.model.document import Document
import requests
import json

class ClientUserRole(Document):
    def before_save(self):
        """Set the name of the document to the user name and sync roles to central server."""
        base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link')
        if self.flags.in_insert:
            return
        
        roles = [row.role for row in self.roles if row.role]

        client_name = frappe.get_single("SigzenBI Subscription Settings").client_name

        payload = {
            "user": self.user,
            "client_name": client_name,
            "roles": roles,
            "action": "add/update"
        }

        url = f"{base_url}api/method/sigzenbi_central.API.sync_user_role.update_user_roles"

        try:
            response = requests.post(url, json=payload)

            if response.status_code != 200:
                frappe.throw(f"Failed to update user roles: {response.text}")

            result = response.json()
            if result.get("message", {}).get("status") == "error":
                frappe.throw(f"API error: {result['message'].get('message')}")

        except Exception as e:
            frappe.throw(f"Error contacting central server: {str(e)}")

    def on_trash(self):
        """Clear user roles on the central server when this document is deleted."""

        client_name = frappe.get_single("SigzenBI Subscription Settings").client_name
        base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link')
        payload = {
            "user": self.user,
            "client_name": client_name,
            "roles": [],  # Clear roles on delete
            "action": "delete"
        }

        url = f"{base_url}api/method/sigzenbi_central.API.sync_user_role.update_user_roles"

        try:
            response = requests.post(url, json=payload)

            if response.status_code != 200:
                frappe.throw(f"Failed to delete user roles: {response.text}")

            result = response.json()
            if result.get("message", {}).get("status") == "error":
                frappe.throw(f"API error: {result['message'].get('message')}")

        except Exception as e:
            frappe.throw(f"Error contacting central server: {str(e)}")
