import frappe
import requests

# Need to change this url to the one that is used in the sigzenbi_central app
url = "http://127.0.0.1:8000/api/method/sigzenbi_central.API.fetch_database_credentials.get_database_credentials"

client_name = frappe.db.get_single_value("Global Defaults", "default_company")

db_host = frappe.conf.get("db_host")
db_name = frappe.conf.get("db_name")
db_password = frappe.conf.get("db_password")
db_user = frappe.conf.get("db_user")

payload = {
    "client_name": client_name,
    "db_host": db_host,
    "db_name": db_name,
    "db_user": db_name,
    "db_password": db_password
}

response = requests.post(url, data=payload)

print(response.json())
