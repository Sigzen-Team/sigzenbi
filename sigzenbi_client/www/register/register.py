# pyrefly: ignore [missing-import]
import frappe
import frappe.sessions
import requests
import json

# NEVER CACHE THIS PAGE. Frappe caches rendered www pages on path+language only --
# no user -- so a cached copy is served to EVERYONE. This page renders a per-session csrf_token into the signup form.
# Module level, not context.no_cache: the renderer reads it off the module, so it
# still applies on a path that returns or redirects early.
no_cache = True

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

    # Signup is OPERATOR-ONLY (founder decision 2026-09-02): registration binds this whole
    # site's identity, and the very next setup step (databasereg) already demands a System
    # Manager -- letting a Guest start what only an operator can finish produced half-done
    # signups. Soft-render a login prompt (this is a page, not an API).
    from sigzenbi_client.www.databasereg.databasereg import require_site_operator
    try:
        require_site_operator()
    except frappe.PermissionError:
        context.central_html = (
            "<div style='max-width:640px;margin:80px auto;font-family:system-ui,sans-serif;text-align:center'>"
            "<h1 style='font-size:1.4rem'>Administrator login required</h1>"
            "<p>SigzenBI signup can only be completed by a <b>System Manager</b> of this ERPNext site.</p>"
            "<p><a href='/login?redirect-to=/portal/signup'>Log in</a> and try again.</p></div>"
        )
        return context

    base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link') or ''
    if base_url and not base_url.endswith('/'):
        base_url += '/'
    context.central_url = base_url

    # The ~45 lines that stood here fetched the whole plan catalogue from Central on every
    # render, just to guess an "Entity Type" from a plan name via keyword lists. The Entity
    # Type field is gone (it fed Customer.customer_type, which no BI code reads) and the
    # plan-picker it came from was retired by the trial-first signup. Removing it also
    # removes a synchronous cross-server HTTP call from the page load.

    # Prefill what this site already knows. The signup form runs ON the customer's own
    # ERPNext: the organisation name is its default Company, and when the visitor is signed
    # in, their name and email are on their User record. Asking them to retype all of it was
    # not just friction -- typing a DIFFERENT organisation name silently creates a BI tenant
    # whose identity does not match the ERPNext it is reporting on. Every value stays
    # editable, and a signed-out visitor simply gets empty fields as before.
    # Global Defaults directly, NOT frappe.defaults.get_global_default("company"): despite
    # the name that helper resolves USER-scoped defaults first and returns nothing for a
    # signed-out visitor, which is precisely who is looking at this form. Falls back to the
    # only Company on the site when the default is unset.
    companies = frappe.get_all("Company", limit=2, pluck="name")
    context.prefill_org = (frappe.db.get_single_value("Global Defaults", "default_company")
                           or (companies[0] if len(companies) == 1 else "") or "")
    context.prefill_first_name = context.prefill_last_name = context.prefill_email = ""
    visitor = frappe.session.user
    if visitor and visitor not in ("Guest", "Administrator"):
        user = frappe.db.get_value(
            "User", visitor, ["first_name", "last_name", "email"], as_dict=True) or {}
        context.prefill_first_name = user.get("first_name") or ""
        context.prefill_last_name = user.get("last_name") or ""
        context.prefill_email = user.get("email") or visitor

    context.csrf_token = frappe.sessions.get_csrf_token()

    # Pass the local/proxy API URL to central's register.html so it is called relative to client.
    # There is only ONE endpoint to rewrite now: signup used to make a second call to activate the
    # trial (fetch_client_subscription), which Central folded into registration itself on
    # 2026-09-02. The rewrite for it went with the call — a rewrite for a string the template no
    # longer contains is dead code that reads like wiring.
    context.api_get_credentials_url = "/api/method/sigzenbi_client.www.register.register.get_client_credentials"
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


class _RegistrationRejected(Exception):
    """A local refusal (not a crash) that must still undo the tenant Central just created.
    Its message is shown to the visitor; every other exception gets the generic reply."""


def _rollback_central_registration(base_url, client_name, api_key, api_secret):
    """Ask Central to delete the tenant it just created for us, because we could not
    finish our half of the signup.

    SERVER-TO-SERVER, authenticated by the api_key/api_secret Central issued with this very
    registration -- so this can only ever delete the tenant that was just handed to us.
    Best-effort by construction: the visitor is already being told the signup failed, and a
    Central that is unreachable now cannot be made reachable by failing louder. What it must
    never do is raise into the caller's error path and replace a clear message with a
    stack trace, so every failure here is logged and swallowed.
    """
    if not (base_url and client_name and api_key and api_secret):
        return
    try:
        response = requests.post(
            f"{base_url}api/method/sigzenbi_central.API.fetch_client_credentials.rollback_registration",
            json={"client_name": client_name, "api_key": api_key, "api_secret": api_secret},
            timeout=30,
        )
        result = parse_response(response)
        if result.get("status") != "success":
            frappe.log_error(
                title="register: Central rollback did not succeed",
                message=f"client_name={client_name}\n{result.get('message')}",
            )
    except Exception as e:
        # No secrets in the message -- Error Log is broadly readable.
        frappe.log_error(
            title="register: Central rollback call failed",
            message=f"client_name={client_name}\n{e}",
        )


@frappe.whitelist(allow_guest=True)
def get_client_credentials(**kwargs):
    # OPERATOR-ONLY (founder decision 2026-09-02). This replaces the old trust-on-first-use
    # bootstrap: an operator's authenticated ERPNext session is real proof this caller may
    # bind the site's identity, which TOFU never was. The re-registration guard below stays
    # as defense in depth. Raised OUTSIDE the try so it surfaces as a clean 403, not a
    # {"status": "error"} payload.
    from sigzenbi_client.www.databasereg.databasereg import require_site_operator
    require_site_operator()
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

            # ATOMIC SIGNUP, CLIENT HALF (2026-09-02). Central's registration is now
            # all-or-nothing inside its own transaction, but that guarantee stops at its
            # transaction boundary: by the time we get here Central has COMMITTED a complete
            # tenant, and every step below can still fail. Each one that does used to leave a
            # tenant on Central that this site would never use and nobody would ever clean up.
            #
            # So every post-Central step lives in this one block, and any failure in it calls
            # the compensating delete on Central with the credentials Central just issued.
            # There is no partial success: either this site is registered and wired up, or the
            # tenant does not exist on either side.
            try:
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
                #
                # This refusal is INSIDE the rollback block on purpose: refusing the response
                # is exactly the case where Central holds a tenant nobody will ever claim.
                if settings.client_name and settings.client_name != client_name:
                    frappe.log_error(
                        title="register.get_client_credentials: blocked re-registration",
                        message=(
                            f"Refused to overwrite existing client_name '{settings.client_name}' "
                            f"with '{client_name}' from an unauthenticated registration call."
                        ),
                    )
                    raise _RegistrationRejected(
                        "This site is already registered. Contact support to change the "
                        "registered account."
                    )

                if client_name:
                    settings.client_name = client_name
                # The trial is activated by registration itself now, so its status arrives in
                # THIS response instead of needing a second, operator-gated call the signup
                # visitor was never allowed to make. Three setup pages gate on this field.
                settings.subscription_status = parsed.get("subscription_status") or "Active"
                # First-registration bootstrap is a sanctioned writer of the guarded
                # client_name field -- go through the guard's provisioning escape hatch.
                # Without it the guard throws HERE, after Central has already committed
                # the registration, orphaning the Central records (live 2026-09-02).
                frappe.flags.sigzen_settings_provisioning = True
                try:
                    settings.save(ignore_permissions=True)
                finally:
                    frappe.flags.sigzen_settings_provisioning = False

                # Credentials are stored per-client_name (SigzenBI Client Credential),
                # not on the shared singleton — see credentials.py. The singleton keeps
                # only the client_name field, as the site-primary identity marker.
                if client_name and api_key and api_secret:
                    from sigzenbi_client import credentials as client_credentials
                    client_credentials.upsert_root(client_name, api_key, api_secret, "registration")

                # COMMIT LAST, once every local write has succeeded. Before this the settings
                # save committed on its own and upsert_root ran after it, so a failure storing
                # the credentials left this site holding a client_name it had no credentials
                # for -- registered and unable to talk to Central.
                frappe.db.commit()
            except Exception as local_err:
                frappe.db.rollback()
                _rollback_central_registration(base_url, client_name, api_key, api_secret)
                frappe.log_error(
                    title="register.get_client_credentials: local step failed, Central rolled back",
                    message=f"client_name={client_name}\n{local_err}\n\n{frappe.get_traceback()}",
                )
                return {
                    "status": "error",
                    "message": (str(local_err) if isinstance(local_err, _RegistrationRejected)
                                else "We couldn't finish setting up this site. Nothing was "
                                     "saved — please try again."),
                }

            # Establish the Central session from the sid Central returned with the
            # registration it just performed. This replaced a second POST /api/method/login
            # that re-sent the user's password -- the only thing that password was ever
            # used for after signup, and the reason the form demanded one. Central returns
            # the sid ONLY when it actually created the user, never on the
            # already-registered branch, so this cannot be used to adopt someone's account.
            #
            # AFTER the rollback block, not inside it: the registration is complete and
            # durable on both sides by now, and a cookie that failed to set is a re-login,
            # not a reason to delete a live tenant.
            central_sid = parsed.get("sid")
            if central_sid and central_sid != "Guest":
                frappe.local.cookie_manager.set_cookie("central_sid", central_sid, httponly=True, samesite="Lax", secure=True)
                frappe.local.cookie_manager.set_cookie("client_session_user", kwargs.get("email"), httponly=True, samesite="Lax", secure=True)

        return parsed
    except Exception as e:
        frappe.log_error(title="Credentials Proxy Error", message=f"Get Client Credentials Proxy Error: {e}")
        return {"status": "error", "message": str(e)}


@frappe.whitelist(allow_guest=True)
def fetch_client_subscription(**kwargs):
    # Operator-only: this call runs with this site's stored Central credentials and flips
    # the local `subscription_status` that three setup pages gate on. Unlike
    # get_client_credentials() above -- whose first-registration bootstrap genuinely cannot
    # be authenticated -- this one only ever runs on an already-registered site, so there is
    # no bootstrap case to preserve.
    #
    # THAT CLAIM ONLY BECAME TRUE ON 2026-09-02, and the gap is worth recording. The signup
    # page used to call this as its own second step, so a GUEST visitor hit it on every new
    # tenant and was correctly 403'd by the gate below -- which is why signup ended with
    # "Subscription activation failed" over a site that was already registered. The trial is
    # now activated by Central inside registration itself, which is what finally removed the
    # bootstrap caller and left the gate unconditionally correct. Keep it exactly as it is.
    from sigzenbi_client.www.databasereg.databasereg import require_site_operator

    require_site_operator()
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
            # The plan name and term used to be mirrored here too. They were a STALE COPY of
            # state Central owns -- the billing page fetches the live version and overwrites
            # them on every render -- so they were removed with the fields on 2026-08-16.
            # `subscription_status` stays: three setup pages gate on it.
            settings.subscription_status = "Active"
            settings.save(ignore_permissions=True)
            frappe.db.commit()
            
        return parsed
    except Exception as e:
        frappe.log_error(title="Subscription Proxy Error", message=f"Fetch Client Subscription Proxy Error: {e}")
        return {"status": "error", "message": str(e)}
