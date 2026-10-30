import frappe


def execute():
	"""v15 only: remove the SigzenBI Desktop Icon rows that every migrate used to add.

	v15's Desktop Icon has no icon_type and its desk never shows these rows, so all of them are
	junk (qa-varmora15 had 37, most under the old sigzenbi_client app name). v16 keeps its icon.
	"""
	if frappe.get_meta("Desktop Icon").has_field("icon_type"):
		return
	frappe.db.delete("Desktop Icon", {"label": "SigzenBI", "app": ("in", ["sigzenbi", "sigzenbi_client"])})
