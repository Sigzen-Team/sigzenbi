# pyrefly: ignore [missing-import]
from sigzenbi_client.www.client_login import render_bi_login


def get_context(context):
    # Thin wrapper: the audited approach-C resolution order lives in render_bi_login
    # (client_login.py), reused verbatim so /portal/login and the retired /client_login
    # never diverge. Never inline auth logic here.
    return render_bi_login(context)
