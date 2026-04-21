// Copyright (c) 2025, Kalp Dalsania and contributors
// For license information, please see license.txt

frappe.ui.form.on('SigzenBI Role Client', {
    refresh: function(frm) {
        frm.add_custom_button(__('Fetch Permissions'), async function () {
            try {
                const response = await frappe.call({
                    method: 'sigzenbi_client.API.fetch_and_update_permissions.fetch_and_update_permissions',
                    args: {}
                });

                if (response.message && response.message.status === "success") {
                    frappe.show_alert({
                        message: __(`Successfully updated ${response.message.inserted_count} permissions.`),
                        indicator: "green"
                    });
                } else {
                    frappe.msgprint(__('Failed to fetch or update permissions: ') + (response.message?.error || 'Unknown error'));
                }
            } catch (error) {
                console.error("Client error:", error);
                frappe.msgprint(__('Error while fetching permissions: ') + error.message);
            }
        });
    }
});