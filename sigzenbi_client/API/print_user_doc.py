import frappe

@frappe.whitelist(allow_guest=True)
def run():
    doc = frappe.get_doc("User", "param@gmail.com")
    print(frappe.as_json(doc))
