// Copyright (c) 2026, Parin Dave and contributors
// For license information, please see license.txt

frappe.ui.form.on("SigzenBI Subscription Plan", {
    refresh(frm) {
        frm.add_custom_button(__('Fetch Subscription Details'), async function () {
            if (!frm.doc.client_name) {
                frappe.msgprint(__('Please set Client Name first.'));
                return;
            }

            const csrfToken = frappe.csrf_token;
            const API_URL = "http://172.22.206.232:8003/api/method/sigzenbi_central.API.send_subscription_details.send_subscription_details";
            const API_KEY = "2444eb73c70d250";
            const API_SECRET = "892b6a6f7860ceb";
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

                    frm.set_value('subscription_id', subscriptionDetails.subscription_id);
                    frm.set_value('subscription_plan_name', subscriptionDetails.plan_name);
                    frm.set_value('client_name', frm.doc.client_name);
                    frm.set_value('licence_no', subscriptionDetails.licence_no || '');
                    frm.set_value('subscription_start_date', subscriptionDetails.start_date);
                    frm.set_value('subscription_end_date', subscriptionDetails.end_date);
                    frm.set_value('currency_vmhj', subscriptionDetails.plan_price);
                    frm.set_value('subscription_status', subscriptionDetails.status);
                    frm.set_value('max_users', subscriptionDetails.max_users);
                    frm.set_value('current_users', subscriptionDetails.current_users || 0);
                    frm.set_value('security_key', subscriptionDetails.security_key);
                    frappe.msgprint(__('Subscription details fetched successfully.'));
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
