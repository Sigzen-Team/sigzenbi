import frappe

@frappe.whitelist(allow_guest=True)
def fetch_first_user(user_name, client_name, first_name, last_name, email, password):
    try:
        full_name = f"{first_name} {last_name}"

        # 1. Set client name in SigzenBI Subscription Settings (Single DocType)
        frappe.db.set_value("SigzenBI Subscription Settings", None, "client_name", client_name)

        # 2. Check if User exists; if not, create one
        existing_user = frappe.db.get_value("User", {"email": email})
        if not existing_user:
            frappe.db.sql("""
                INSERT IGNORE INTO `tabUser` (name, email, first_name, last_name, user_type, enabled, creation, modified, owner)
                VALUES (%s, %s, %s, %s, 'Website User', 1, NOW(), NOW(), 'Administrator')
            """, (email, email, first_name, last_name))
            
        # Set password for standard Frappe LoginManager authentication
        from frappe.utils.password import update_password
        update_password(email, password)

        # 3. Insert into SigzenBI Users
        frappe.db.sql("""
            UPDATE user_name=%s, full_name=%s, user_id=%s, password=%s
        """, (email, us INSERT INTO `tabSigzenBI Users` (name, user_name, full_name, user_id, password, creation, modified, owner)
            VALUES (%s, %s, %s, %s, %s, NOW(), NOW(), 'Administrator')
            ON DUPLICATE KEYer_name, full_name, email, password, user_name, full_name, email, password))

        # 4. Insert into Client User Role with "Default" role
        # Create parent doc
        frappe.db.sql("""
            INSERT IGNORE INTO `tabClient User Role` (name, user, creation, modified, owner)
            VALUES (%s, %s, NOW(), NOW(), 'Administrator')
        """, (email, email))

        # Insert child row into Client User Role's table (assuming table fieldname is `roles`)
        if not frappe.db.exists("BI Role Client", {"parent": email, "role": "Default"}):
            child_name = frappe.generate_hash(length=10)
            frappe.db.sql("""
                INSERT INTO `tabBI Role Client` (name, parent, parenttype, parentfield, role, creation, modified, owner, idx)
                VALUES (%s, %s, 'Client User Role', 'roles', 'Default', NOW(), NOW(), 'Administrator', 1)
            """, (child_name, email))

        # 5. Update role in SigzenBI Users to user's email
        frappe.db.sql("""
            UPDATE `tabSigzenBI Users`
            SET role = %s
            WHERE name = %s
        """, (email, email))

        frappe.db.commit()

        return {
            "status": "success",
            "message": f"User {email} created and linked successfully."
        }

    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(title="fetch_first_user", message=frappe.get_traceback())
        return {
            "status": "error",
            "message": f"An error occurred: {str(e)}"
        }
