# pyrefly: ignore [missing-import]
import frappe
import frappe.sessions
import requests
import json

def get_context(context):
    # /register/register is retired as a page — BI signup lives at /portal/signup.
    # Unconditional 301 there, forwarding the ?plan= query so the prefill survives.
    # Target host/path are static; only the (host-neutral) query string is request-derived.
    from sigzenbi_client.utils import redirect_without_port
    qs = ""
    if getattr(frappe.local, "request", None) and frappe.request.query_string:
        qs = frappe.request.query_string.decode()
    redirect_without_port("/portal/signup" + (("?" + qs) if qs else ""))


def render_signup(context):
    if "sigzenbi_client" not in frappe.get_installed_apps():
        try:
            from frappe.installer import install_app
            install_app("sigzenbi_client")
            frappe.db.commit()
            frappe.log_error(title="App Installer", message="Successfully installed sigzenbi_client programmatically!")
        except Exception as e:
            import traceback
            frappe.log_error(title="App Installer", message=f"Failed programmatically installing sigzenbi_client: {e}\n{traceback.format_exc()}")

    # Already-registered guard: once this site has a primary client_name, signup is done.
    # Re-showing the self-serve form is confusing and a re-submit re-provisions/overwrites,
    # so send an already-registered visitor straight to the login instead of the form.
    if frappe.db.get_single_value('SigzenBI Subscription Settings', 'client_name'):
        frappe.local.flags.redirect_location = "/portal/login"
        raise frappe.Redirect

    base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
    if base_url and not base_url.endswith('/'):
        base_url += '/'
    context.central_url = base_url

    # Dynamic prefill matching logic for any custom plan
    matched_entity_type = ""
    selected_plan = frappe.form_dict.get("plan")
    if selected_plan and base_url:
        try:
            # Fetch plans list from central
            plans_url = f"{base_url}api/method/sigzenbi_central.API.send_subscription_plan.send_subscription_plan"
            plans_res = requests.post(plans_url, timeout=10)
            plans_data = plans_res.json()
            if plans_data.get("message", {}).get("status") == "success":
                plans_list = plans_data["message"].get("subscription_plan", [])
                
                # Normalize selected plan name (e.g. "partnership_firm" -> "partnership firm")
                norm_selected = selected_plan.lower().replace("_", " ").strip()
                
                # Find matching plan doc
                matched_plan = None
                for plan in plans_list:
                    plan_name = plan.get("name", "").lower().strip()
                    if plan_name == norm_selected or plan_name.replace(" ", "_") == norm_selected:
                        matched_plan = plan
                        break
                
                if matched_plan:
                    plan_name_lower = matched_plan.get("name", "").lower()
                    custom_no_of_users = matched_plan.get("custom_no_of_users") or 0
                    
                    partnership_kws = ["partnership", "partner", "joint", "associate", "associates", "llp", "collab", "firm"]
                    company_kws = ["company", "enterprise", "corporate", "corporation", "business", "organization", "group", "team", "ltd", "inc", "agency", "commercial", "unlimited", "suite", "co", "elite", "platinum", "gold", "growth", "multi", "sme", "smb", "startup"]
                    
                    if custom_no_of_users > 1:
                        if any(kw in plan_name_lower for kw in partnership_kws):
                            matched_entity_type = "Partnership"
                        else:
                            matched_entity_type = "Company"
                    else:
                        if any(kw in plan_name_lower for kw in company_kws):
                            matched_entity_type = "Company"
                        else:
                            matched_entity_type = "Individual"
        except Exception as e:
            frappe.log_error(title="register_prefill_matching_error", message=f"Prefill matching failed: {e}")

    context.csrf_token = frappe.sessions.get_csrf_token()

    # Pass the local/proxy API URLs to central's register.html so they are called relative to client
    context.api_get_credentials_url = "/api/method/sigzenbi_client.www.register.register.get_client_credentials"
    context.api_fetch_subscription_url = "/api/method/sigzenbi_client.www.register.register.fetch_client_subscription"
    context.plans_url = "/client_plans"

    central_html = ""
    # Fetch from HTTP
    if base_url:
        try:
            # Method fetch (not a public-page GET) so Central's /register/register page
            # can be closed. Same pattern as the login-template mirror in client_login.py.
            url = f"{base_url}api/method/sigzenbi_central.www.register.register.get_register_template"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                try:
                    central_html = response.json().get("message", response.text)
                except Exception:
                    central_html = response.text
        except Exception as e:
            frappe.log_error(title="register", message=f"Error fetching central register.html: {e}")
                
    if not central_html:
        from sigzenbi_client.utils import guided_fallback
        context.central_html = guided_fallback("The registration form", bool(base_url))
    else:
        # Rewrite asset URLs to point to central server
        if base_url:
            central_html = central_html.replace('"/assets/', f'"{base_url}assets/')
            central_html = central_html.replace("'/assets/", f"'{base_url}assets/")
            central_html = central_html.replace('url(/assets/', f'url({base_url}assets/')
            central_html = central_html.replace('url("/assets/', f'url("{base_url}assets/')
            central_html = central_html.replace("url('/assets/", f"url('{base_url}assets/")
            
            # Rewrite hardcoded API endpoints to use Jinja tags
            central_html = central_html.replace(
                "'/api/method/sigzenbi_central.API.fetch_client_credentials.get_client_credentials'",
                "'{{ api_get_credentials_url }}'"
            )
            central_html = central_html.replace(
                "'/api/method/sigzenbi_central.API.fetch_client_subscription.fetch_client_subscription'",
                "'{{ api_fetch_subscription_url }}'"
            )
            # Expand keyword matching for auto-selecting Entity Type
            central_html = central_html.replace(
                "const companyKeywords = ['company',",
                "const companyKeywords = ['sme', 'smb', 'startup', 'company',"
            )
            # Inject server-side resolved matched entity type
            if matched_entity_type:
                central_html = central_html.replace(
                    'let matched = "";',
                    f'let matched = "{matched_entity_type}";'
                )

        from sigzenbi_client.utils import rewrite_plans_link
        central_html = rewrite_plans_link(central_html)

        # Pre-render the central HTML template with context so Jinja tags are executed
        try:
            context.central_html = frappe.render_template(central_html, context)
        except Exception as e:
            frappe.log_error(title="register", message=f"Error rendering central register template: {e}")
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
            
        return {"status": "error", "message": error_msg}
        
    if "message" in res_json:
        return res_json["message"]
    return res_json


@frappe.whitelist(allow_guest=True)
def get_client_credentials(**kwargs):
    try:
        base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
        if base_url and not base_url.endswith('/'):
            base_url += '/'
        
        kwargs.pop("cmd", None)

        # Retrieve and inject dynamic client site URL and port
        from sigzenbi_client.utils import get_client_url_and_port
        client_url, client_port = get_client_url_and_port()
        kwargs["client_url"] = client_url
        kwargs["client_site_port"] = client_port

        url = f"{base_url}api/method/sigzenbi_central.API.fetch_client_credentials.get_client_credentials"
        response = requests.post(url, json=kwargs, timeout=120)
        
        parsed = parse_response(response)

        if parsed.get("status") == "success":
            api_key = parsed.get("api_key")
            api_secret = parsed.get("api_secret")
            client_name = parsed.get("client_name") or kwargs.get("client_name")

            settings = frappe.get_single("SigzenBI Subscription Settings")

            # This is a guest-callable endpoint: Central's own registration is
            # intentionally public/self-serve, so "Central said success" alone
            # does not prove the caller is this site's legitimate operator.
            # Once this site has already been registered against a client_name,
            # a fresh, differently-authenticated success response (e.g. from an
            # attacker who self-registered their own account on Central) must
            # not be allowed to silently overwrite the shared credentials every
            # other identity on this bench depends on. Only the very first
            # registration (no client_name set yet) is trusted on sight — that
            # bootstrap moment can't be authenticated any other way, matching
            # how SSH trusts a host key on first connection.
            if settings.client_name and settings.client_name != client_name:
                frappe.log_error(
                    title="register.get_client_credentials: blocked re-registration",
                    message=(
                        f"Refused to overwrite existing client_name '{settings.client_name}' "
                        f"with '{client_name}' from an unauthenticated registration call."
                    ),
                )
                return {
                    "status": "error",
                    "message": "This site is already registered. Contact support to change the registered account.",
                }

            if client_name:
                settings.client_name = client_name
            settings.save(ignore_permissions=True)
            frappe.db.commit()

            # Credentials are stored per-client_name (SigzenBI Client Credential),
            # not on the shared singleton — see credentials.py. The singleton keeps
            # only the client_name field, as the site-primary identity marker.
            if client_name and api_key and api_secret:
                from sigzenbi_client import credentials as client_credentials
                client_credentials.upsert_root(client_name, api_key, api_secret, "registration")

            # Automatically log in the user on the central server to establish a session
            try:
                login_url = f"{base_url}api/method/login"
                login_res = requests.post(login_url, json={"usr": kwargs.get("email"), "pwd": kwargs.get("password")}, timeout=10)
                if login_res.status_code == 200:
                    # Extract the non-Guest sid cookie
                    central_sid = None
                    for cookie in login_res.cookies:
                        if cookie.name == "sid" and cookie.value != "Guest":
                            central_sid = cookie.value
                            break
                    if central_sid:
                        frappe.local.cookie_manager.set_cookie("central_sid", central_sid, httponly=True, samesite="Lax", secure=True)
                        frappe.local.cookie_manager.set_cookie("client_session_user", kwargs.get("email"), httponly=True, samesite="Lax", secure=True)
            except Exception as login_e:
                frappe.log_error(title="auto_login_error", message=str(login_e))
            
        return parsed
    except Exception as e:
        frappe.log_error(title="Credentials Proxy Error", message=f"Get Client Credentials Proxy Error: {e}")
        return {"status": "error", "message": str(e)}


@frappe.whitelist(allow_guest=True)
def fetch_client_subscription(**kwargs):
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

        url = f"{base_url}api/method/sigzenbi_central.API.fetch_client_subscription.fetch_client_subscription"
        from sigzenbi_client.utils import call_central_api
        parsed = call_central_api(url, payload=kwargs, method="POST", timeout=120, client_name=kwargs.get("client_name"))
        
        if parsed.get("status") == "success":
            settings = frappe.get_single("SigzenBI Subscription Settings")
            settings.subscription_plan_name = kwargs.get("subscription_plan") or "Active Plan"
            settings.subscription_status = "Active"
            settings.save(ignore_permissions=True)
            frappe.db.commit()
            
        return parsed
    except Exception as e:
        frappe.log_error(title="Subscription Proxy Error", message=f"Fetch Client Subscription Proxy Error: {e}")
        return {"status": "error", "message": str(e)}
