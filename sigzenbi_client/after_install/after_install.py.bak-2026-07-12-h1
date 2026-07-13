import frappe

SIGZENBI_CENTRAL_URL = "https://central.sigzenbi.com"
SIGZENBI_SUPERSET_URL = "https://bi.sigzenbi.com"

def create_default_permissions_and_roles():
    create_permission_client()
    create_role_client()
    set_default_subscription_settings()
    setup_gateway_secret()

def set_default_subscription_settings():
    frappe.db.set_value("SigzenBI Subscription Settings", None, {
        "sigzenbi_erp_link": SIGZENBI_CENTRAL_URL,
        "sigzenbi_link": SIGZENBI_SUPERSET_URL,
    })
    frappe.db.commit()

def setup_gateway_secret():
    if not frappe.conf.get("sigzen_gateway_shared_secret"):
        from frappe.installer import update_site_config
        unique_secret = frappe.generate_hash(length=42)
        update_site_config("sigzen_gateway_shared_secret", unique_secret)

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