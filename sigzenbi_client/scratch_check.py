import frappe

def run():
    print("USER TYPE:", frappe.db.get_value("User", "param@gmail.com", "user_type") if frappe.db.exists("User", "param@gmail.com") else "User not found")
    print("ROLES of param@gmail.com:", frappe.get_roles("param@gmail.com") if frappe.db.exists("User", "param@gmail.com") else "N/A")
    print("Portal Settings default_portal_home:", frappe.db.get_single_value('Portal Settings', 'default_portal_home'))
    print("Website Settings home_page:", frappe.db.get_single_value('Website Settings', 'home_page'))
    print("hooks.py home_page:", frappe.get_hooks('home_page'))
    print("hooks.py website_user_home_page:", frappe.get_hooks('website_user_home_page'))
    print("hooks.py role_home_page:", frappe.get_hooks('role_home_page'))
