import frappe

@frappe.whitelist(allow_guest=True)
def run():
    print("CHECKING AUTH FOR param@gmail.com:")
    # Check if entry exists in __Auth
    pwd_exists = frappe.db.sql("SELECT name FROM `__Auth` WHERE doctype='User' AND fieldname='password' AND name='param@gmail.com'")
    print("Password in __Auth:", pwd_exists)
