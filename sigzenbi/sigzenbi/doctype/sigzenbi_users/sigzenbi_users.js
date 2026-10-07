// Copyright (c) 2026, Parin Dave and contributors
// For license information, please see license.txt

frappe.ui.form.on("SigzenBI Users", {
    refresh(frm) {
    },

    user_name: function (frm) {
        if (frm.doc.user_name) {
            frappe.db.get_value('User', frm.doc.user_name, 'full_name', (r) => {
                if (r && r.full_name) {
                    frm.set_value('full_name', r.full_name);
                } else {
                    frappe.msgprint(__('No full name found for the selected user.'));
                    frm.set_value('full_name', '');
                }
            });

            frm.set_value('user_id', frm.doc.user_name);
        } else {
            frm.set_value('full_name', '');
            frm.set_value('user_id', '');
        }
    }
});
