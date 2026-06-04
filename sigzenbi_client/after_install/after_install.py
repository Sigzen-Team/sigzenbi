import frappe

def create_default_permissions_and_roles():
    create_permission_client()
    create_role_client()
    set_subscription_settings()

def create_permission_client():
    # Check if the permission already exists
    if not frappe.db.exists("SigzenBI Permission Client", "can_info User"):
        frappe.db.sql("""
            INSERT INTO `tabSigzenBI Permission Client` (name, permission, creation, modified, owner)
            VALUES (%s, %s, NOW(), NOW(), %s)
        """, ("can_info User", "can_info User", frappe.session.user))
        frappe.db.commit()

def create_role_client():
    if not frappe.db.exists("SigzenBI Role Client", "Default"):
        # Insert the main role record
        frappe.db.sql("""
            INSERT INTO `tabSigzenBI Role Client` (name, role_name, creation, modified, owner)
            VALUES (%s, %s, NOW(), NOW(), %s)
        """, ("Default", "Default", frappe.session.user))

        # Insert the child permission table entry
        # Assuming the child table name is `tabSigzenBI Role Client Permission` and the parentfield is 'permissions'
        # child_name = frappe.generate_hash(length=10)
        # frappe.db.sql("""
        #     INSERT INTO `tabSigzenBI Role Client Permission` (name, parent, parentfield, parenttype, permission, creation, modified, owner, idx)
        #     VALUES (%s, %s, 'permissions', 'SigzenBI Role Client', %s, NOW(), NOW(), %s, 1)
        # """, (child_name, "Default", "can_info User", frappe.session.user))

        # frappe.db.commit()

def set_subscription_settings():
    """
    Set default values for the SigzenBI Subscription Settings single doctype
    """
    try:
        # Load the singleton doc
        settings = frappe.get_single("SigzenBI Subscription Settings")
        settings.sigzenbi_link = "http://127.0.0.1:8088"  # Change to production default if needed
        settings.sigzenbi_erp_link = "http://127.0.0.1:8007"  # Change to production default if needed
        settings.save(ignore_permissions=True)
        frappe.db.commit()
    except Exception as e:
        frappe.log_error(title="Failed to set SigzenBI Subscription Settings", message=frappe.get_traceback())