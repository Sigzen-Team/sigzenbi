// Copyright (c) 2026, Parin Dave and contributors
// For license information, please see license.txt

frappe.ui.form.on("SigzenBI Subscription Settings", {
    refresh(frm) {
        frm.add_custom_button(__('Fetch Subscription Details'), async function () {
            if (!frm.doc.client_name) {
                frappe.msgprint(__('Please set Client Name first.'));
                return;
            }

            try {
                const responseData = await frappe.xcall(
                    "sigzenbi_client.sigzenbi_client.doctype.sigzenbi_subscription_settings.sigzenbi_subscription_settings.fetch_subscription_details",
                    {
                        client_name: frm.doc.client_name
                    }
                );

                if (responseData.message && typeof responseData.message === 'string') {
                    frappe.msgprint(__(responseData.message));
                } else if (responseData.message) {
                    const subscriptionDetails = responseData.message;
                    frappe.call({
                        method: "frappe.client.get_count",
                        args: {
                            doctype: "SigzenBI Users",
                        },
                        callback: function (res) {
                            if (res.message !== undefined) {
                                const current_users_count = res.message;

                                frappe.call({
                                    method: "frappe.client.set_value",
                                    args: {
                                        doctype: "SigzenBI Subscription Settings",
                                        name: "SigzenBI Subscription Settings",
                                        fieldname: {
                                            subscription_id: subscriptionDetails.subscription_id,
                                            subscription_plan_name: subscriptionDetails.plan_name,
                                            client_name: frm.doc.client_name,
                                            licence_no: subscriptionDetails.licence_no || '',
                                            subscription_start_date: subscriptionDetails.start_date,
                                            subscription_end_date: subscriptionDetails.end_date,
                                            currency_vmhj: subscriptionDetails.plan_price,
                                            subscription_status: subscriptionDetails.status,
                                            max_users: subscriptionDetails.max_users,
                                            security_key: subscriptionDetails.security_key,
                                            api_key: subscriptionDetails.api_key,
                                            api_secret: subscriptionDetails.api_secret,
                                            licence_no: subscriptionDetails.subscription_id,
                                            current_users: current_users_count,
                                            sigzenbi_link: subscriptionDetails.sigzenbi_link,
                                        }
                                    },
                                    callback: function (response) {
                                        frappe.show_alert({
                                            message: __('Subscription details fetched successfully.'),
                                            indicator: 'green'
                                        });
                                        frm.reload_doc();
                                    }
                                });
                            } else {
                                frappe.msgprint(__('Failed to get current user count.'));
                            }
                        }
                    });
                } else {
                    frappe.msgprint(__('Unexpected response from server.'));
                }
            } catch (error) {
                console.error("Fetch error:", error);
                frappe.msgprint(__('Failed to fetch subscription details.'));
            }
        });
    },
});
