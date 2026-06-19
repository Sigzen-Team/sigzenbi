import frappe

@frappe.whitelist(allow_guest=True)
def fetch_first_user(user_name, client_name, first_name, last_name, email, password):
    try:
        full_name = f"{first_name} {last_name}"

        # 1. Set client name in SigzenBI Subscription Settings (Single DocType)
        frappe.db.set_value("SigzenBI Subscription Settings", None, "client_name", client_name)

        # 2. Check if User exists; if not, create one
        if not frappe.db.exists("User", email):
            user_doc = frappe.get_doc({
                "doctype": "User",
                "email": email,
                "first_name": first_name,
                "last_name": last_name or "",
                "user_type": "Website User",
                "send_welcome_email": 0
            })
            user_doc.insert(ignore_permissions=True)
            
        # Set password for standard Frappe LoginManager authentication
        from frappe.utils.password import update_password
        update_password(email, password)

        # 3. Insert or update SigzenBI Users
        if frappe.db.exists("SigzenBI Users", email):
            user_doc = frappe.get_doc("SigzenBI Users", email)
            user_doc.user_name = email
            user_doc.full_name = full_name
            user_doc.user_id = email
            user_doc.password = password
            user_doc.save(ignore_permissions=True)
        else:
            frappe.get_doc({
                "doctype": "SigzenBI Users",
                "user_name": email,
                "full_name": full_name,
                "user_id": email,
                "password": password,
            }).insert(ignore_permissions=True)

        # 4. Ensure Client User Role document exists with "Default" role
        if not frappe.db.exists("Client User Role", email):
            client_role_doc = frappe.get_doc({
                "doctype": "Client User Role",
                "user": email,
                "roles": [{"role": "Default"}]
            })
            client_role_doc.insert(ignore_permissions=True)
        else:
            client_role_doc = frappe.get_doc("Client User Role", email)
            has_default = any(row.role == "Default" for row in client_role_doc.roles)
            if not has_default:
                client_role_doc.append("roles", {"role": "Default"})
                client_role_doc.save(ignore_permissions=True)

        # 5. Update role in SigzenBI Users to user's email
        frappe.db.set_value("SigzenBI Users", email, "role", email)

        frappe.db.commit()

        return {
            "status": "success",
            "message": f"User {email} created and linked successfully."
        }

    except Exception as e:
        frappe.db.rollback()
        
        # Extract messages from the message log if str(e) is empty (typical for Frappe ValidationError)
        log_messages = []
        if getattr(frappe.local, "message_log", None):
            for msg in frappe.local.message_log:
                if isinstance(msg, dict):
                    log_messages.append(msg.get("message") or str(msg))
                else:
                    log_messages.append(str(msg))
                    
        err_msg = str(e)
        if not err_msg and log_messages:
            err_msg = ", ".join(log_messages)
        if not err_msg:
            err_msg = "Unknown error or Validation Error occurred"

        frappe.log_error(title="fetch_first_user", message=frappe.get_traceback())
        return {
            "status": "error",
            "message": f"An error occurred: {err_msg}"
        }

