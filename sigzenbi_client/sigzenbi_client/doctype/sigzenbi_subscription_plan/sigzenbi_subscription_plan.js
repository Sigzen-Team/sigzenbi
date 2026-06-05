// Copyright (c) 2026, Parin Dave and contributors
// For license information, please see license.txt

frappe.ui.form.on("SigzenBI Subscription Plan", {
    refresh(frm) {
        frm.add_custom_button(__('Fetch Subscription Details'), function () {
            if (!frm.doc.client_name) {
                frappe.msgprint(__('Please set Client Name first.'));
                return;
            }

            frappe.call({
                method: "sigzenbi_client.sigzenbi_client.doctype.sigzenbi_subscription_settings.sigzenbi_subscription_settings.fetch_subscription_details",
                args: {
                    client_name: frm.doc.client_name
                },
                freeze: true,
                callback: function (r) {
                    if (r.message) {
                        const centralResponse = r.message;
                        const details = centralResponse.message;

                        if (details && typeof details === 'string') {
                            frappe.msgprint(__(details));
                        } else if (details) {
                            frm.set_value('subscription_id', details.subscription_id);
                            frm.set_value('subscription_plan_name', details.plan_name);
                            frm.set_value('client_name', frm.doc.client_name);
                            frm.set_value('licence_no', details.licence_no || '');
                            frm.set_value('subscription_start_date', details.start_date);
                            frm.set_value('subscription_end_date', details.end_date);
                            frm.set_value('currency_vmhj', details.plan_price);
                            frm.set_value('subscription_status', details.status);
                            frm.set_value('max_users', details.max_users);
                            frm.set_value('current_users', details.current_users || 0);
                            frm.set_value('security_key', details.security_key);
                            frappe.msgprint(__('Subscription details fetched successfully.'));
                        } else {
                            frappe.msgprint(__('Unexpected response from server.'));
                        }
                    } else {
                        frappe.msgprint(__('Failed to fetch subscription details.'));
                    }
                }
            });
        });
    },
});
