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
    set_default_subscription_settings()
    try:
        ensure_desktop_icon()
    except Exception:
        # A desk-tile failure must never block app install.
        frappe.log_error(title="SigzenBI Desktop Icon", message=frappe.get_traceback())
    try:
        ensure_workspace_sidebar()
    except Exception:
        # Same rule: a sidebar failure must never block install or migrate.
        frappe.log_error(title="SigzenBI Workspace Sidebar", message=frappe.get_traceback())
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
    # standard=1 IS DELIBERATE, and do not "fix" the churn it causes.
    #
    # Every `bench migrate` deletes this row: sync.remove_orphan_doctypes sweeps
    # `Desktop Icon` where {"standard": True} and finds no matching FILE in the app, so it
    # drops ours -- you will see "Deleting entity Desktop Icon SigzenBI" in the migrate log.
    # after_migrate then runs and this function puts it straight back, so the net state after
    # any migrate is correct.
    #
    # The obvious "fix" (standard=0, to survive the sweep) BREAKS IT: get_desktop_icons filters
    # `standard == 1 OR (standard == 0 AND owner IN (Administrator, current_user))`, so a
    # standard=0 icon created during migrate is owned by Administrator and becomes invisible to
    # every tenant user -- the exact bug we were fixing. Leave it at 1 and let it be recreated.
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

# The module's sidebar, in the order a person actually works: the workspace first, then the
# records they open. Only doctypes that code actually READS live here -- the role chain
# (Client User Role / BI Role Client / SigzenBI Role Client) was retired on 2026-08-16 because
# nothing ever read it to make a decision.
SIDEBAR_ITEMS = [
    ("SigzenBI", "Workspace", "SigzenBI", "wallpaper"),
    ("Subscription Settings", "DocType", "SigzenBI Subscription Settings", "setting"),
    ("Client Credential", "DocType", "SigzenBI Client Credential", "lock-keyhole"),
    ("Users", "DocType", "SigzenBI Users", "user"),
]


def ensure_workspace_sidebar():
    """Create the module's Workspace Sidebar so Frappe stops auto-generating one.

    MUST be named after the MODULE: auto_generate_sidebar_from_module() suppresses itself with
    `exists("Workspace Sidebar", {"name": module, "for_user": None})`. A record named after the
    workspace ("SigzenBI") would leave the guessed sidebar in place.

    Idempotent, and it REPAIRS an existing record whose items have drifted, so a box that was
    set up before this existed converges on the next migrate.
    """
    module = "SigzenBI Client"
    if not frappe.db.exists("Module Def", module):
        return

    wanted = [
        {"label": label, "link_type": link_type, "link_to": link_to,
         "type": "Link", "icon": icon, "collapsible": 1}
        for label, link_type, link_to, icon in SIDEBAR_ITEMS
        # Never advertise something this site does not have -- a sidebar entry that 404s is
        # worse than a missing one.
        if link_type != "DocType" or frappe.db.exists("DocType", link_to)
    ]

    if frappe.db.exists("Workspace Sidebar", module):
        doc = frappe.get_doc("Workspace Sidebar", module)
        current = [(i.label, i.link_type, i.link_to) for i in doc.items]
        if current == [(w["label"], w["link_type"], w["link_to"]) for w in wanted]:
            return                      # already correct; do not churn `modified`
        doc.items = []
    else:
        doc = frappe.new_doc("Workspace Sidebar")
        doc.name = module

    doc.title = module
    doc.module = module
    doc.header_icon = "bar-chart"
    for w in wanted:
        doc.append("items", w)
    doc.flags.ignore_permissions = True
    doc.save() if not doc.is_new() else doc.insert(ignore_permissions=True)
    frappe.db.commit()
