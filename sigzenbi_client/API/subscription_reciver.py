import frappe
from frappe import _
import datetime

@frappe.whitelist(allow_guest=True)
def subscription_reciver(
    subscription_id,
    subscription_name,
    subscription_start_date,
    subscription_end_date,
    subscription_amount,
    subscription_status,
    max_user,
    security_key,
    api_key,
    api_secret,
    user_name,
    user_email,
    user_id,
    password,
    client_name
):  
    try:
        # Store subscription data in SigzenBI Subscription Settings (Single Doctype)
        frappe.flags.ignore_permissions = True
        frappe.db.set_value("SigzenBI Subscription Settings", None, "subscription_id", subscription_id)
        frappe.db.set_value("SigzenBI Subscription Settings", None, "subscription_plan_name", subscription_name)
        frappe.db.set_value("SigzenBI Subscription Settings", None, "subscription_start_date", datetime.datetime.strptime(subscription_start_date, "%Y-%m-%d").date())
        frappe.db.set_value("SigzenBI Subscription Settings", None, "subscription_end_date", datetime.datetime.strptime(subscription_end_date, "%Y-%m-%d").date())
        frappe.db.set_value("SigzenBI Subscription Settings", None, "subscription_status", subscription_status)
        frappe.db.set_value("SigzenBI Subscription Settings", None, "max_users", max_user)
        frappe.db.set_value("SigzenBI Subscription Settings", None, "security_key", security_key)
        frappe.db.set_value("SigzenBI Subscription Settings", None, "api_key", api_key)
        frappe.db.set_value("SigzenBI Subscription Settings", None, "api_secret", api_secret)
        frappe.db.set_value("SigzenBI Subscription Settings", None, "client_name", client_name)
        frappe.db.set_value("SigzenBI Subscription Settings", None, "currency_vmhj", subscription_amount)
        frappe.db.set_value("SigzenBI Subscription Settings", None, "licence_no", subscription_id)

        # Create or update user in SigzenBI Users with role Admin
        if frappe.db.exists("SigzenBI Users", {"user_id": user_id}):
            user_doc = frappe.get_doc("SigzenBI Users", {"user_id": user_id})
            user_doc.user_name = user_name
            user_doc.email = user_email
            user_doc.password = password
            user_doc.role = "Admin"
            user_doc.save(ignore_permissions=True)
        else:
            new_user = frappe.get_doc({
                "doctype": "SigzenBI Users",
                "user_name": user_name,
                "email": user_email,
                "user_id": user_id,
                "password": password,
                "role": "Admin"
            })
            new_user.insert(ignore_permissions=True)

        return {
            "status": "success",
            "message": _("Subscription data and admin user created/updated successfully.")
        }

    except Exception as e:
        frappe.log_error(title="Subscription Receiver Error", message=frappe.get_traceback())
        return {
            "status": "error",
            "message": str(e)
        }
