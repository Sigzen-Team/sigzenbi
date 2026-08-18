# Copyright (c) 2026, Parin Dave and contributors
# For license information, please see license.txt

# pyrefly: ignore [missing-import]
import frappe
# pyrefly: ignore [missing-import]
from frappe.model.document import Document
import requests
from frappe.utils.password import get_decrypted_password

class SigzenBIUsers(Document):
    def sync_with_central_server(self, action):
        """Synchronize user data with the central server."""
        settings = frappe.get_single("SigzenBI Subscription Settings")
        base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link')
        if base_url and not base_url.endswith("/"):
            base_url += "/"

        # Always None. This looked up a user whose `role` == "Admin", but every writer set
        # `role` to the user's own email, so it never matched once. Kept as a payload key
        # (frozen wire format) with an honest value instead of a query that cannot succeed.
        admin_user_name = None

        # Note: Hardcoded URL should be configurable (e.g., stored in settings) in production
        url = f"{base_url}api/method/sigzenbi_central.API.user_sync.sync_client_user"
        payload = {
            "action": action,
            "user_data": {
                "client_name": settings.client_name.strip() if settings.client_name else None,
                "user_name": self.user_name,
                "full_name": self.full_name if " " in (self.full_name or "").strip() else f"{(self.full_name or '')} .",
                "user_id": self.user_id,
                # field removed 2026-08-16; key kept for the frozen wire format
                "role": None,
                "client_user": admin_user_name,
            }
        }
        
        try:
            from sigzenbi_client.utils import call_central_api
            response = call_central_api(url, payload=payload, method="POST", timeout=10)
            
            if response.get("status") == "error":
                frappe.msgprint(response.get("message"))
        except Exception as e:
            frappe.log_error(title="SigzenBI User Sync Failed", message=str(e))

    def before_insert(self):
        """Check user limit and uniqueness before inserting a new user."""
        if frappe.flags.in_fetch_first_user:
            return

        settings = frappe.get_single("SigzenBI Subscription Settings")

        # Prevent duplicate registration by email
        if self.user_id and frappe.db.exists("SigzenBI Users", {"user_id": self.user_id}):
            frappe.throw(f"A user with email '{self.user_id}' is already registered.")

        # SEATS ARE ENFORCED ON CENTRAL (seat model + enforce_caps + assert_can_grant).
        # The local cap that stood here was gated on a settings value of 0, so it could never
        # fire -- a duplicate cap that had never once rejected anybody. Removed with the
        # field on 2026-08-16.

    def after_insert(self):
        """Update user count and sync with central server after insertion."""
        settings = frappe.get_single("SigzenBI Subscription Settings")
        settings.save(ignore_permissions=True)
        self.sync_with_central_server(action="create")
        self.reload()
    
    def before_save(self):
        # Track only meaningful changes
        old = self.get_doc_before_save()

        if not old:
            return  # This is a new document, let on_insert handle it

        fields_to_track = ["full_name", "user_id", "password"]
        changed = any(getattr(old, field) != getattr(self, field) for field in fields_to_track)

        if changed:
            self.sync_with_central_server(action="update")

    
    def on_trash(self):
        """Sync deletion with central server and update user count."""
        # Save important fields BEFORE the document is gone
        user_name = self.user_name
        full_name = self.full_name
        user_id = self.user_id
        role = None    # field removed 2026-08-16

        settings = frappe.get_single("SigzenBI Subscription Settings")

        # Always None. This looked up a user whose `role` == "Admin", but every writer set
        # `role` to the user's own email, so it never matched once. Kept as a payload key
        # (frozen wire format) with an honest value instead of a query that cannot succeed.
        admin_user_name = None
                
        payload = {
            "action": "delete",
            "user_data": {
                "client_name": settings.client_name.strip() if settings.client_name else None,
                "user_name": user_name,
                "full_name": full_name if " " in (full_name or "").strip() else f"{(full_name or '')} .",
                "user_id": user_id,
                "role": role,
                "client_user": admin_user_name,
            }
        }

        try:
            base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link')
            if base_url and not base_url.endswith("/"):
                base_url += "/"
            url = f"{base_url}api/method/sigzenbi_central.API.user_sync.sync_client_user"
            from sigzenbi_client.utils import call_central_api
            call_central_api(url, payload=payload, method="POST", timeout=10)
        except Exception as e:
            frappe.log_error(title="SigzenBI User Sync Failed on Deletion", message=str(e))

        # Now update user count (subtract 1)
        settings.save(ignore_permissions=True)
        
