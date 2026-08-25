app_name = "sigzenbi_client"
app_title = "SigzenBI Client"
app_publisher = "SigzenBI"
app_description = "Dashboards and plain-English answers from your ERPNext data"
app_email = "info@sigzenbi.com"
app_license = "gpl-3.0"

# Apps
# ------------------

required_apps = ["erpnext"]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "sigzenbi_client",
# 		"logo": "/assets/sigzenbi_client/logo.png",
# 		"title": "SigzenBI Client",
# 		"route": "/sigzenbi_client",
# 		"has_permission": "sigzenbi_client.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/sigzenbi_client/css/sigzenbi_client.css"
# app_include_js = "/assets/sigzenbi_client/js/sigzenbi_client.js"

# include js, css files in header of web template
# web_include_css = "/assets/sigzenbi_client/css/sigzenbi_client.css"
# web_include_js = "/assets/sigzenbi_client/js/sigzenbi_client.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "sigzenbi_client/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "sigzenbi_client/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# no home_page hook: it would override the customer site's own website homepage (and Marketplace review forbids base-page overrides). Our hosted boxes pin Website Settings.home_page = "login" as site data instead.


# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "sigzenbi_client.utils.jinja_methods",
# 	"filters": "sigzenbi_client.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "sigzenbi_client.install.before_install"
after_install = "sigzenbi_client.after_install.after_install.after_install"
# ALSO on every migrate. after_install fires once, at first install, so boxes installed before
# ensure_desktop_icon()/the seeders existed never got them -- which is exactly why the Desk tile
# was missing while the Workspace was present. The routine is idempotent, so re-running it is
# free and makes the desk tile + default role self-healing.
after_migrate = "sigzenbi_client.after_install.after_install.after_install"

# Uninstallation
# ------------

# before_uninstall = "sigzenbi_client.uninstall.before_uninstall"
# after_uninstall = "sigzenbi_client.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "sigzenbi_client.utils.before_app_install"
# after_app_install = "sigzenbi_client.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "sigzenbi_client.utils.before_app_uninstall"
# after_app_uninstall = "sigzenbi_client.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "sigzenbi_client.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Member-scope cache invalidation (SPEC-member-row-security 3.2): these are the SAME
# doctypes Frappe itself invalidates permissions on. Each event enqueues ONE post to
# Central's bust_member_scope so a permission edit bites on the next dashboard render
# instead of after the 60s TTL. Enqueue-only + never-raise: see API/gateway/bust_scope.py.
_BUST = "sigzenbi_client.API.gateway.bust_scope.on_permission_change"
doc_events = {
	"User Permission": {"on_update": _BUST, "on_trash": _BUST},
	"DocShare": {"on_update": _BUST, "on_trash": _BUST},
	"User": {"on_update": _BUST, "on_trash": _BUST},
	"DocPerm": {"on_update": _BUST, "on_trash": _BUST},
	"Custom DocPerm": {"on_update": _BUST, "on_trash": _BUST},
	"Server Script": {"on_update": _BUST, "on_trash": _BUST},
	"System Settings": {"on_update": _BUST},
}

# Scheduled Tasks
# ---------------
scheduler_events = {
	"all": [
		"sigzenbi_client.API.gateway.poll_jobs.check_and_start_polling_loop"
	],
	"cron": {
		"0 * * * *": ["sigzenbi_client.API.gateway.poll_jobs.materialize_all_clients"],
	},
}

# Testing
# -------

# before_tests = "sigzenbi_client.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "sigzenbi_client.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "sigzenbi_client.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["sigzenbi_client.utils.before_request"]
# after_request = ["sigzenbi_client.utils.after_request"]

# Job Events
# ----------
# before_job = ["sigzenbi_client.utils.before_job"]
# after_job = ["sigzenbi_client.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"sigzenbi_client.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }


# --- SigzenBI app card on the Desk apps screen (mirrors india_compliance) ---
add_to_apps_screen = [{"name":"sigzenbi_client","logo":"/assets/sigzenbi_client/images/sigzenbi.svg","title":"SigzenBI","route":"/desk/sigzenbi","has_permission":"sigzenbi_client.check_app_permission"}]
