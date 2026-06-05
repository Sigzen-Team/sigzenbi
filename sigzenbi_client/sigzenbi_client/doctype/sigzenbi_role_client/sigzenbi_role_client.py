import frappe
import requests
import json
from frappe.model.document import Document
import datetime

class SigzenBIRoleClient(Document):
    def autoname(self):
        """Generate a unique name for the role based on client name and role name."""   
        client_name = frappe.db.get_single_value("SigzenBI Subscription Settings", "client_name")
        if client_name:
            client_name = client_name.strip()
        client_name = client_name.replace(" ", "_") if client_name else "default_client"
        self.name = f"{client_name}_{self.role_name}"
    def get_client_name(self):
        """Retrieve the client name from settings."""
        return frappe.db.get_single_value("SigzenBI Subscription Settings", "client_name")

    def after_insert(self):
        """Sync new record with server and store server-generated name."""
        result = self.sync_to_server("create")
        if isinstance(result, dict) and result.get("status") == "success" and result.get("name"):
            # Store server name in a separate field instead of overwriting self.name
            self.name = result["name"]
            self.save(ignore_permissions=True)
        else:
            frappe.msgprint("Failed to sync the new record with the server.")

    def on_update(self):
        """Sync updates to server if record is already synced."""
        if hasattr(self, 'name') and self.name:
            result = self.sync_to_server("update")
            if not result:
                frappe.msgprint(f"Failed to update the server for {self.role_name}.")
        else:
            frappe.msgprint("Record not yet synced to server; sync will be attempted on next save.")

    def on_trash(self):
        """Delete record from server if synced."""
        if self.name:
            result = self.sync_to_server("delete")
            if not result:
                frappe.msgprint(f"Failed to delete the record from the server for {self.role_name}.")
        else:
            frappe.msgprint("Record not yet synced to server; attempting sync now...")
            self.sync_to_server("create")  # Trigger sync if not already done
            if self.name:
                self.sync_to_server("delete")  # Proceed with deletion after syncing
            else:
                frappe.msgprint("Failed to sync before deletion.")


    def sync_to_server(self, operation):
        server_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link')
        client_name = self.get_client_name()
        if client_name:
            client_name = client_name.strip()
        api_key = frappe.db.get_single_value("SigzenBI Subscription Settings", "api_key")
        api_secret = frappe.db.get_single_value("SigzenBI Subscription Settings", "api_secret")

        if not server_url:
            frappe.throw("Server URL is not configured in SigzenBI Subscription Settings")
        if not client_name:
            frappe.throw("Client Name is not configured in SigzenBI Subscription Settings")

        if not server_url.startswith(("http://", "https://")):
            server_url = f"http://{server_url}"
        
        if not server_url.endswith("/"):
            server_url += "/"

        headers = {
            "Content-Type": "application/json"
        }

        # Clean the permissions and convert datetime fields to string
        permissions_clean = []
        for row in self.permissions:
            row_dict = row.as_dict()
            for key, value in row_dict.items():
                if isinstance(value, datetime.datetime):  # Corrected to datetime.datetime
                    row_dict[key] = value.strftime("%Y-%m-%d %H:%M:%S")
            row_dict.pop("name", None)
            permissions_clean.append(row_dict)

        data = {
            "role_name": self.role_name,
            "permissions": permissions_clean,
            "client_name": client_name,
            "operation": operation
        }

        # Use name for update and delete operations
        if operation in ["update", "delete"]:
            if not hasattr(self, 'name') or not self.name:
                frappe.throw(f"Cannot perform {operation} without name")
            data["name"] = self.name  # Send name instead of self.name

        try:
            frappe.log_error(
                title="Client Sync Request",
                message=f"Sending {operation} request to {server_url}\nPayload: {json.dumps(data, indent=2)}"
            )

            response = requests.post(
                f"{server_url}api/method/sigzenbi_central.API.permission_sync.sync_permission",
                data=json.dumps(data),
                headers=headers
            )
            response.raise_for_status()

            frappe.log_error(title="Sync Response Raw", message=f"Raw Response Text: {response.text}")

            try:
                result = response.json()
            except json.JSONDecodeError:
                frappe.log_error(title="Client Sync Error", message=f"Non-JSON response: {response.text}")
                frappe.throw("Server returned invalid JSON response")

            server_message = result.get("message", {})
            if server_message.get("status") != "success":
                error_message = server_message.get("message", "Unknown error")
                frappe.log_error(title="Client Sync Error", message=f"Server Sync Failed: {error_message}\nResponse: {json.dumps(result, indent=2)}")
                frappe.throw(f"Server Sync Failed: {error_message}")

            frappe.log_error(title="Client Sync Success", message=f"Successful {operation}: {server_message.get('message')}")

            if operation == "create":
                return server_message

            return True

        except requests.exceptions.RequestException as e:
            frappe.log_error(title="Client Sync Error", message=f"Request failed: {str(e)}\nResponse: {getattr(e.response, 'text', 'No response')}")
            frappe.throw(f"Failed to sync with server: {str(e)}")

        except Exception as e:
            frappe.log_error(title="Client Sync Error", message=f"Unexpected error: {str(e)}")
            frappe.throw(f"Unexpected error occurred: {str(e)}")
            