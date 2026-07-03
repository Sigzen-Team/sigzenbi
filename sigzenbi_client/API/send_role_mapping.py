import frappe

@frappe.whitelist(allow_guest=True)
def send_role_mapping(user_name):
    """Send role mapping to the client."""
    try:
        central_sid = frappe.request.cookies.get("central_sid") if getattr(frappe.local, "request", None) else None
        from sigzenbi_client.utils import resolve_authenticated_user
        caller = resolve_authenticated_user(central_sid)
        if not caller or caller != user_name:
            return {"status": "error", "message": "Not permitted"}

        # Log the start of the process
        frappe.logger().info(f"Fetching role mapping for user: {user_name}")

        # Get the role mapping data for the given user_name
        role_mapping = frappe.get_all(
            "BI Role Client",
            fields=["role"],
            filters={"parent": user_name}
        )

        # Extract only the 'role' values into a list
        role_list = [role['role'] for role in role_mapping]

        # Log the role list
        frappe.logger().info(f"Role list for user {user_name}: {role_list}")

        if role_list:
            return {"status": "success", "roles": role_list}
        else:
            # Log if no roles were found
            frappe.logger().warning(f"No roles found for user {user_name}")
            return {"status": "warning", "message": f"No roles found for user {user_name}"}
    except Exception as e:
        # Log any exceptions that occur
        frappe.logger().error(f"Error occurred while fetching role mapping for user {user_name}: {str(e)}")
        return {"status": "error", "message": str(e)}
