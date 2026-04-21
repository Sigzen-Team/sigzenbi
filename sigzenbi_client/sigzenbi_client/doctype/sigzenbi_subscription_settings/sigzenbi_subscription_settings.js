// Copyright (c) 2025, Kalp Dalsania and contributors
// For license information, please see license.txt

frappe.ui.form.on("SigzenBI Subscription Settings", {
	refresh(frm) {
        frm.add_custom_button(__('Fetch Subscription Details'), async function() {
            if (!frm.doc.client_name) {
                frappe.msgprint(__('Please set Client Name first.'));
                return;
            }

            const csrfToken = frappe.csrf_token;
            const API_URL = "http://127.0.0.1:8000/api/method/sigzenbi_central.API.send_subscription_details.send_subscription_details";
            const API_KEY = "3b87f054c9b1a06";
            const API_SECRET = "8822a4b0438e433";
            try {
                const response = await fetch(API_URL, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "Authorization": `token ${API_KEY}:${API_SECRET}`,
                        "X-Frappe-CSRF-Token": csrfToken
                    },
                    body: JSON.stringify({
                        client_name: frm.doc.client_name
                    })
                });

                const responseData = await response.json();

                if (responseData.message && typeof responseData.message === 'string') {
                    frappe.msgprint(__(responseData.message));
                } else if (responseData.message) {
                    const subscriptionDetails = responseData.message;
                    frappe.call({
                        method: "frappe.client.get_count",
                        args: {
                            doctype: "SigzenBI Users",
                        },
                        callback: function(res) {
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
                                    callback: function(response) {
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
