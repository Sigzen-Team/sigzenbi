frappe.provide('frappe.search.utils');

$(document).on('toolbar_setup', function() {
	if (frappe.search && frappe.search.utils && frappe.search.utils.make_function_searchable) {
		frappe.search.utils.make_function_searchable(function() {
			window.location.href = "/client_login";
		}, __("sigzenbi login"));
	}
});
