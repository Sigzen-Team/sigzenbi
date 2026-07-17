import frappe

# The SigzenBI Central hub + shared Superset URLs. These are PRODUCT constants (the one
# hosted hub every client agent talks to) — NOT customer-domain values. A customer's own
# domain is never hardcoded anywhere: it is derived from the live request at runtime
# (see utils.get_browser_base_url / dashboard_api embed_origin). A self-hosted / white-label
# deployment overrides these via site_config keys (sigzenbi_central_url / sigzenbi_superset_url)
# or, post-install, via install_agent.sh --central-url (register_agent.run).
DEFAULT_CENTRAL_URL = "https://sigzenbi-central.sigzenone.com"
DEFAULT_SUPERSET_URL = "https://superset.sigzenone.com"


def _central_url():
    return (frappe.conf.get("sigzenbi_central_url") or DEFAULT_CENTRAL_URL).rstrip("/")


def _superset_url():
    return (frappe.conf.get("sigzenbi_superset_url") or DEFAULT_SUPERSET_URL).rstrip("/")


def create_default_permissions_and_roles():
    create_permission_client()
    create_role_client()
    set_default_subscription_settings()
    try:
        ensure_desktop_icon()
    except Exception:
        # A desk-tile failure must never block app install.
        frappe.log_error(title="SigzenBI Desktop Icon", message=frappe.get_traceback())
    # NOTE: no gateway-secret generation at install (C3-completion). The old setup_gateway_secret()
    # minted a RANDOM sigzen_gateway_shared_secret that could never match Central's global — which
    # silently broke per-tenant secret DISTRIBUTION on every fresh self-serve install (the box2 417
    # root cause). Auth is now purely per-tenant: the tenant receives its own gateway_secret at
    # registration (fetch_first_user), authenticated by its api_secret. No shared/global secret.

def ensure_desktop_icon():
    """Create the 'SigzenBI' App Desktop Icon so the tile shows on the Desk /app grid, driven by
    the add_to_apps_screen hook. Frappe's own after_app_install DOES call create_desktop_icons(),
    but its bulk App-icon existence check is broken (tries to re-insert existing App icons ->
    IntegrityError on multi-app installs), so it can crash before reaching this app. Do it here,
    idempotently. The Workspace it links to (/desk/sigzenbi) ships as a synced file under
    sigzenbi_client/workspace/sigzenbi/. Safe to re-run."""
    if frappe.db.exists("Desktop Icon", {"app": "sigzenbi_client", "icon_type": "App"}):
        return
    details = frappe.get_hooks("add_to_apps_screen", app_name="sigzenbi_client")
    if not details:
        return
    d = details[0]
    label = d.get("title") or "SigzenBI"
    if frappe.db.exists("Desktop Icon", label):
        return
    icon = frappe.new_doc("Desktop Icon")
    icon.label = label
    icon.icon_type = "App"
    icon.link_type = "External"
    icon.app = "sigzenbi_client"
    icon.link = d.get("route")
    icon.logo_url = d.get("logo")
    icon.standard = 1
    icon.hidden = 0
    icon.insert(ignore_permissions=True)
    frappe.cache.delete_key("desktop_icons")
    frappe.db.commit()


def set_default_subscription_settings():
    # Idempotent + fail-forward: only SEED the hub URLs when unset, so a re-install or an
    # installer-supplied value (install_agent.sh / register_agent) is never clobbered back to
    # the default. The old code unconditionally wrote a DEAD default (central.sigzenbi.com /
    # bi.sigzenbi.com), silently pointing every fresh install at a non-existent hub so nothing
    # worked until someone hand-edited the setting.
    current_central = frappe.db.get_single_value("SigzenBI Subscription Settings", "sigzenbi_erp_link")
    current_superset = frappe.db.get_single_value("SigzenBI Subscription Settings", "sigzenbi_link")
    updates = {}
    if not (current_central or "").strip():
        updates["sigzenbi_erp_link"] = _central_url()
    if not (current_superset or "").strip():
        updates["sigzenbi_link"] = _superset_url()
    if updates:
        frappe.db.set_value("SigzenBI Subscription Settings", None, updates)
        frappe.db.commit()

def create_permission_client():
    # Check if the permission already exists
    if not frappe.db.exists("SigzenBI Permission Client", "can_info User"):
        frappe.db.sql("""
            INSERT INTO `tabSigzenBI Permission Client` (name, permission, creation, modified, owner)
            VALUES (%s, %s, NOW(), NOW(), %s)
        """, ("can_info User", "can_info User", frappe.session.user))
        frappe.db.commit()

def create_role_client():
    if not frappe.db.exists("SigzenBI Role Client", "Default"):
        # Insert the main role record
        frappe.db.sql("""
            INSERT INTO `tabSigzenBI Role Client` (name, role_name, creation, modified, owner)
            VALUES (%s, %s, NOW(), NOW(), %s)
        """, ("Default", "Default", frappe.session.user))
