// Copyright (c) 2026, Parin Dave and contributors
// For license information, please see license.txt

frappe.ui.form.on("SigzenBI", {
    onload: function (frm) {
        window.location.href = "http://sigzenbi_client.:8004/test_client_home";
    }
});
