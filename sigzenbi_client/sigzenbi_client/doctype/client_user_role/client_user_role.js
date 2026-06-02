// Copyright (c) 2026, Parin Dave and contributors
// For license information, please see license.txt

frappe.ui.form.on("Client User Role", {
    refresh(frm) {
        frm.fields_dict['roles'].grid.get_field('role').get_query = function (doc, cdt, cdn) {
            let selected_roles = [];

            // Loop through the roles in the current form, but do not exclude the current row
            (frm.doc.roles || []).forEach(function (row) {
                // Avoid including the current row's value while editing
                if (row.role && row.name !== cdn) {
                    selected_roles.push(row.role);
                }
            });


            return {
                filters: {
                    name: ['not in', selected_roles]
                }
            };
        };
    },
});
