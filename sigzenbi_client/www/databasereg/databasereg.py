# pyrefly: ignore [missing-import]
import frappe
import frappe.sessions
import requests
import json
import re


def _get_client_site_db_config():
	"""Database credentials from this client bench site (sites/<site>/site_config.json)."""
	return {
		"db_host": frappe.conf.get("db_host") or "127.0.0.1",
		"db_name": frappe.conf.db_name,
		"db_user": frappe.conf.get("db_user") or frappe.conf.db_name,
		"db_password": frappe.conf.db_password,
	}


def _inject_client_db_fields(html):
	"""Replace central pre-rendered DB inputs with this client site's credentials."""
	field_map = {
		"db_host": "{{ auto_db_host }}",
		"db_name": "{{ auto_db_name }}",
		"db_user": "{{ auto_db_user }}",
		"db_password": "{{ auto_db_password }}",
		"client_name": "{{ auto_client_name }}",
	}
	for field, jinja_value in field_map.items():
		html = re.sub(
			rf'(<input[^>]*name="{field}"[^>]*value=")[^"]*(")',
			rf"\1{jinja_value}\2",
			html,
			count=1,
			flags=re.IGNORECASE | re.DOTALL,
		)
	return html


def _rewrite_database_api_url(html, base_url, browser_base_url, client_api_url):
	"""Point form submit to client proxy, not central (avoids CORS + wrong DB)."""
	central_api_suffix = "api/method/sigzenbi_central.API.fetch_database_credentials.get_database_credentials"
	paths = [
		f"'/{central_api_suffix}'",
		f'"/{central_api_suffix}"',
		f"'{base_url}{central_api_suffix}'",
		f'"{base_url}{central_api_suffix}"',
	]
	if browser_base_url != base_url:
		paths.extend([
			f"'{browser_base_url}{central_api_suffix}'",
			f'"{browser_base_url}{central_api_suffix}"',
		])
	for central_path in paths:
		html = html.replace(central_path, f"'{client_api_url}'")
	return html


def get_context(context):
    # Ensure client has activated the plan
    status = frappe.db.get_single_value('SigzenBI Subscription Settings', 'subscription_status')
    if status != "Active":
        from sigzenbi_client.utils import redirect_without_port
        redirect_without_port("/register/register")

    base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
    if base_url and not base_url.endswith('/'):
        base_url += '/'
    context.central_url = base_url


    
    # Auto-fetch from this client site's site_config.json (e.g. sites/sigzenbi/site_config.json)
    db_config = _get_client_site_db_config()
    context.auto_db_name = db_config["db_name"]
    context.auto_db_password = db_config["db_password"]
    context.auto_db_host = db_config["db_host"]
    context.auto_db_user = db_config["db_user"]

    # Auto-fill client name from subscription settings so the user doesn't have to type it
    context.auto_client_name = frappe.db.get_single_value('SigzenBI Subscription Settings', 'client_name') or ''

    context.csrf_token = frappe.sessions.get_csrf_token()
    context.api_get_database_credentials_url = "/api/method/sigzenbi_client.www.databasereg.databasereg.get_database_credentials"
    context.plans_url = "/client_plans"

    central_html = ""
    # Fetch from HTTP
    if base_url:
        try:
            url = f"{base_url}databasereg/databasereg"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                central_html = response.text
        except Exception as e:
            frappe.log_error(title="databasereg", message=f"Error fetching central databasereg.html: {e}")
                
    if not central_html:
        context.central_html = "<h1>Could not load database connectivity form.</h1>"
    else:
        # Rewrite asset URLs to point to central server
        if base_url:
            from sigzenbi_client.utils import get_browser_base_url
            browser_base_url = get_browser_base_url(base_url)
            central_html = central_html.replace('"/assets/', f'"{browser_base_url}assets/')
            central_html = central_html.replace("'/assets/", f"'{browser_base_url}assets/")
            central_html = central_html.replace('url(/assets/', f'url({browser_base_url}assets/')
            central_html = central_html.replace('url("/assets/', f'url("{browser_base_url}assets/')
            central_html = central_html.replace("url('/assets/", f"url('{browser_base_url}assets/")
            
            central_html = _rewrite_database_api_url(
                central_html,
                base_url,
                browser_base_url,
                context.api_get_database_credentials_url,
            )
            
            # Rewrite redirect to /thanks to /thankyou for the client app
            central_html = central_html.replace("window.location.href = '/thanks'", "window.location.href = '/thankyou'")
            central_html = central_html.replace('window.location.href = "/thanks"', 'window.location.href = "/thankyou"')
            central_html = central_html.replace("'/thanks'", "'/thankyou'")
            central_html = central_html.replace('"/thanks"', '"/thankyou"')
            central_html = central_html.replace("window.location.href = '/register/thanks'", "window.location.href = '/thankyou'")
            central_html = central_html.replace('window.location.href = "/register/thanks"', 'window.location.href = "/thankyou"')
            central_html = central_html.replace("'/register/thanks'", "'/thankyou'")
            central_html = central_html.replace('"/register/thanks"', '"/thankyou"')

        from sigzenbi_client.utils import rewrite_plans_link
        central_html = rewrite_plans_link(central_html)
        central_html = _inject_client_db_fields(central_html)

        # Pre-render the central HTML template with context so Jinja tags are executed
        try:
            context.central_html = frappe.render_template(central_html, context)
        except Exception as e:
            frappe.log_error(title="databasereg", message=f"Error rendering central databasereg template: {e}")
            context.central_html = central_html  # fallback to raw if template rendering fails
            
    return context


def parse_response(response):
    try:
        res_json = response.json()
    except Exception:
        return {"status": "error", "message": f"Central returned status code {response.status_code}"}
        
    # If the response explicitly returned success, return it directly
    if isinstance(res_json.get("message"), dict) and res_json["message"].get("status") == "success":
        return res_json["message"]
        
    if response.status_code != 200 or "exc" in res_json or "_server_messages" in res_json:
        error_msg = None
        
        # Check for server messages
        if "_server_messages" in res_json:
            try:
                server_msgs = json.loads(res_json["_server_messages"])
                if server_msgs:
                    msg_obj = json.loads(server_msgs[0])
                    # Only treat raise_exception messages as errors if it is an error indicator or raise_exception is True
                    if isinstance(msg_obj, dict):
                        if msg_obj.get("raise_exception") or msg_obj.get("indicator") == "red":
                            error_msg = msg_obj.get("message") or msg_obj
                    else:
                        error_msg = msg_obj
            except Exception:
                pass
                
        # Check for exception
        if not error_msg and "exc" in res_json:
            try:
                exc_msgs = json.loads(res_json["exc"])
                if exc_msgs:
                    error_msg = exc_msgs[0]
            except Exception:
                error_msg = res_json["exc"].split("\n")[0]
               
        if not error_msg:
            # Fallback to general message or error status
            error_msg = res_json.get("message", {}).get("message") if isinstance(res_json.get("message"), dict) else res_json.get("message")
            
        if not error_msg:
            error_msg = "An unknown error occurred on the central server."
            
        # Clean up ugly technical errors for the user interface
        if isinstance(error_msg, str):
            if "Max retries exceeded" in error_msg or "HTTPConnectionPool" in error_msg or "ConnectTimeoutError" in error_msg:
                error_msg = ""
            elif error_msg.startswith("Error Log") or "frappe.exceptions" in error_msg or "Traceback" in error_msg:
                error_msg = ""
            
        return {"status": "error", "message": error_msg}
        
    if "message" in res_json:
        return res_json["message"]
    return res_json


@frappe.whitelist(allow_guest=True)
def get_database_credentials(**kwargs):
    try:
        base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
        if base_url and not base_url.endswith('/'):
            base_url += '/'
        
        # Pop cmd to avoid central routing conflicts
        kwargs.pop("cmd", None)

        # Retrieve and inject dynamic client site URL and port
        from sigzenbi_client.utils import get_client_url_and_port
        client_url, client_port = get_client_url_and_port()
        kwargs["client_url"] = client_url
        kwargs["client_site_port"] = client_port
        
        url = f"{base_url}api/method/sigzenbi_central.API.fetch_database_credentials.get_database_credentials"
        from sigzenbi_client.utils import call_central_api
        parsed = call_central_api(url, payload=kwargs, method="POST", timeout=20, client_name=kwargs.get("client_name"))
        
        # Save registered client name to allow multi-tenant polling
        if isinstance(parsed, dict) and parsed.get("status") == "success":
            client_name = kwargs.get("client_name")
            if client_name:
                res = frappe.db.sql(
                    "SELECT value FROM tabSingles WHERE doctype='SigzenBI Subscription Settings' AND field='registered_client_names'"
                )
                registered_str = res[0][0] if res else ""
                names = [n.strip() for n in registered_str.split(",") if n.strip()]
                if client_name not in names:
                    names.append(client_name)
                    new_str = ",".join(names)
                    frappe.db.sql(
                        "INSERT INTO tabSingles (doctype, field, value) VALUES ('SigzenBI Subscription Settings', 'registered_client_names', %s) "
                        "ON DUPLICATE KEY UPDATE value=%s",
                        [new_str, new_str]
                    )
                    frappe.db.commit()

                # A fresh (or re-confirmed) database registration means this client_name
                # may now have real work to do — clear any stale "no active credential"
                # backoff for it, and the watchdog's active-names cache, so the next
                # scheduler tick doesn't skip spawning its poll loop over stale state.
                from sigzenbi_client.API.gateway.poll_jobs import NO_CREDENTIAL_BACKOFF_KEY, ACTIVE_NAMES_CACHE_KEY

                frappe.cache().delete_value(f"{NO_CREDENTIAL_BACKOFF_KEY}:{client_name}")
                frappe.cache().delete_value(ACTIVE_NAMES_CACHE_KEY)

        return parsed
    except Exception as e:
        frappe.log_error(title="Database Proxy Error", message=f"Get Database Credentials Proxy Error: {e}")
        error_str = str(e)
        if "Max retries exceeded" in error_str or "HTTPConnectionPool" in error_str or "ConnectTimeoutError" in error_str:
            error_str = ""
        return {"status": "error", "message": error_str}
