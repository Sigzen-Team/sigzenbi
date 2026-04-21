# Copyright (c) 2025, Kalp Dalsania and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
import requests
from frappe.utils.password import get_decrypted_password
from cryptography.fernet import Fernet

class SigzenBIUsers(Document):
    def sync_with_central_server(self, action):
        """Synchronize user data with the central server."""
        settings = frappe.get_single("SigzenBI Subscription Settings")
        base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link')
        admin_user_name = frappe.db.get_value(
            "SigzenBI Users",
            {"role": "Admin"},
            "user_name"
        )

        # Note: Hardcoded URL should be configurable (e.g., stored in settings) in production
        url = f"{base_url}api/method/sigzenbi_central.API.user_sync.sync_client_user"
        if action == "update":
            password = self.password
        else:
            password = get_decrypted_password("SigzenBI Users",self.user_name, "password").strip()
            
        payload = {
            "api_key": settings.api_key,
            "api_secret": settings.api_secret,
            "action": action,
            "user_data": {
                "client_name": settings.client_name,
                "user_name": self.user_name,
                "full_name": self.full_name,
                "user_id": self.user_id,
                "role": self.role,
                "password": password,
                "client_user": admin_user_name 
            }
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            response = response.json()
            
            if (response.get("status") == "error"):
                frappe.msgprint(response.get("message"))
            sync_role(self.user_name)  
        except Exception as e:
            frappe.log_error(str(e), "SigzenBI User Sync Failed")

    def before_insert(self):
        """Check user limit before inserting a new user."""
        settings = frappe.get_single("SigzenBI Subscription Settings")
        
        # Count current SigzenBI Users (excludes the user being inserted)
        current_user_count = frappe.db.count("SigzenBI Users")
        
        # Prevent insertion if max_users limit is reached
        if current_user_count >= settings.max_users:
            frappe.throw(f"User limit reached! Maximum allowed users are {settings.max_users}.")

    def after_insert(self):
        """Update user count and sync with central server after insertion."""
        settings = frappe.get_single("SigzenBI Subscription Settings")
        settings.current_users = frappe.db.count("SigzenBI Users")
        settings.save()
        if not frappe.db.exists("Client User Role", self.user_name):
            doc = frappe.get_doc({
            "doctype": "Client User Role",
            "user": self.user_name,
            "roles": [{"role": "Default"}]  
            })
            doc.insert(ignore_permissions=True)
        self.sync_with_central_server(action="create")
        frappe.db.set_value("SigzenBI Users", self.user_name, "role", self.user_name)
        self.reload()
    
    def before_save(self):
        # Track only meaningful changes
        old = self.get_doc_before_save()

        if not old:
            return  # This is a new document, let on_insert handle it

        fields_to_track = ["full_name", "user_id", "role", "password"]
        changed = any(getattr(old, field) != getattr(self, field) for field in fields_to_track)

        if changed:
            self.sync_with_central_server(action="update")

    
    def before_delete(self):
        self.role = None
        # self.save(ignore_permissions=True)
        client_role_doc = frappe.get_all(
            "Client User Role",
            filters={"user_name": self.user_name},
            fields=["name"]
        )
        if client_role_doc:
            frappe.delete_doc("Client User Role", client_role_doc[0].name)
        else:
            frappe.log_error(f"No Client User Role found for user_name: {self.user_name}", "Deletion Warning")
        
        
    def on_trash(self):
        """Sync deletion with central server and update user count."""
        # Save important fields BEFORE the document is gone
        user_name = self.user_name
        full_name = self.full_name
        user_id = self.user_id
        role = self.role
        password = self.password

        settings = frappe.get_single("SigzenBI Subscription Settings")

        admin_user_name = frappe.db.get_value(
            "SigzenBI Users",
            {"role": "Admin"},
            "user_name"
        )
                
        payload = {
            "api_key": settings.api_key,
            "api_secret": settings.api_secret,
            "action": "delete",
            "user_data": {
                "client_name": settings.client_name,
                "user_name": user_name,
                "full_name": full_name,
                "user_id": user_id,
                "role": role,
                "password": password,
                "client_user": admin_user_name 
            }
        }

        try:
            base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link')
            url = f"{base_url}api/method/sigzenbi_central.API.user_sync.sync_client_user"
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            # frappe.delete_doc("Client User Role", user_name)
            frappe.db.sql("DELETE FROM `tabClient User Role` WHERE name = %s", (user_name))
            frappe.db.sql("DELETE FROM `tabBI Role Client` WHERE parent = %s", (user_name))
            # sync_role(self.user_name)
        except Exception as e:
            frappe.log_error(str(e), "SigzenBI User Sync Failed on Deletion")

        # Now update user count (subtract 1)
        settings.current_users = frappe.db.count("SigzenBI Users") - 1
        settings.save()
        
def sync_role(user_name):
    base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link')
    roles = [role.role for role in frappe.get_all('BI Role Client', filters={'parent': user_name}, fields=['role']) if role.role]
    client_name = frappe.get_single("SigzenBI Subscription Settings").client_name

    payload = {
        "user": user_name,
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
        