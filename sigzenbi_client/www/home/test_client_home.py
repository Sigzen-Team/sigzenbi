import frappe
import os
import requests

def get_context(context):
    central_html = ""
    
    # 1. Attempt to load from the local filesystem first (very fast & great for local dev)
    local_path = "/home/parin/sigzen-central/apps/sigzenbi_central/sigzenbi_central/www/home/home.html"
    if os.path.exists(local_path):
        try:
            with open(local_path, "r", encoding="utf-8") as f:
                central_html = f.read()
        except Exception as e:
            frappe.log_error(f"Error reading local central home.html: {e}", "test_client_home")
            
    base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
    if base_url and not base_url.endswith('/'):



        
        base_url += '/'
    context.central_url = base_url
    
    # 2. If not found or failed, fall back to fetching via HTTP/HTTPS URL
    if not central_html:
        if base_url:
            try:
                url = f"{base_url}home"
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    central_html = response.text
                else:
                    url_alt = f"{base_url}home.html"
                    response_alt = requests.get(url_alt, timeout=10)
                    if response_alt.status_code == 200:
                        central_html = response_alt.text
            except Exception as e:
                frappe.log_error(f"Error fetching central home.html via URL: {e}", "test_client_home")
                
    if not central_html:
        context.central_html = "<h1>Could not load central page content.</h1>"
    else:
        try:
            context.central_html = frappe.render_template(central_html, context)
        except Exception as e:
            frappe.log_error(f"Error rendering central home template: {e}", "test_client_home")
            context.central_html = central_html
            
    return context
