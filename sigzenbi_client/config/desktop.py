from frappe import _


def get_data():
    return [
        {
            "module_name": "SigzenBI Client",
            "type": "module",
            "label": _("SigzenBI"),
            "icon": "octicon octicon-graph",
            "color": "#223552",
        }
    ]
