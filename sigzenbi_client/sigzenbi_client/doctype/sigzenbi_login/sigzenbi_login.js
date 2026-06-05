// Copyright (c) 2026, Parin Dave and contributors
// For license information, please see license.txt

frappe.ui.form.on("SigzenBI login", {
    onload: function (frm) {
        window.location.href = "/client_login";
    }
});
