import frappe

def test():
    frappe.init(site="sigzenbi")
    frappe.connect()
    base_url = frappe.db.get_single_value('SigzenBI Subscription Settings', 'sigzenbi_erp_link')
    print("Base URL:", base_url)

if __name__ == "__main__":
    test()
