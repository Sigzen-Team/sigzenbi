import frappe
from sigzenbi_client.www.client_login import get_context

def test():
    frappe.init(site="sigzenbi")
    frappe.connect()
    
    # Mock web request
    frappe.local.request = frappe._dict(method="GET", environ={})
    frappe.local.form_dict = frappe._dict()
    frappe.local.cookie_manager = frappe._dict(set_cookie=lambda *a,**kw: None)
    frappe.local.session_obj = frappe._dict(update=lambda *a,**kw: None)
    frappe.local.session = frappe._dict(data=frappe._dict(csrf_token="test_token"))
    
    context = frappe._dict()
    try:
        get_context(context)
        print("Success:", context)
    except Exception as e:
        import traceback
        traceback.print_exc()
