import frappe
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

@frappe.whitelist(allow_guest=True)
def run():
    print("SIMULATING CLIENT LOGIN REQUEST FOR param@gmail.com:")
    
    # Create request context
    builder = EnvironBuilder(
        path='/api/method/sigzenbi_client.www.client_login.login',
        method='POST',
        environ_overrides={'REMOTE_ADDR': '127.0.0.1'}
    )
    env = builder.get_environ()
    req = Request(env)
    
    # Initialize frappe local context elements
    frappe.local.request = req
    frappe.local.request_ip = "127.0.0.1"
    frappe.local.form_dict = frappe._dict(usr="param@gmail.com", pwd="1234")
    frappe.local.response = frappe._dict()
    frappe.local.error_log = []
    frappe.local.message_log = []
    
    class DummyCookieManager:
        def init_cookies(self):
            pass
        def set_cookie(self, *args, **kwargs):
            pass
    frappe.local.cookie_manager = DummyCookieManager()
    
    try:
        from sigzenbi_client.www.client_login import login
        login(usr="param@gmail.com", pwd="1234")
        print("RESPONSE:", frappe.local.response)
        print("MESSAGE LOG:", frappe.local.message_log)
    except Exception as e:
        import traceback
        print("EXCEPTION TYPE:", type(e))
        print("EXCEPTION STR:", str(e))
        print("TRACEBACK:")
        print(traceback.format_exc())
