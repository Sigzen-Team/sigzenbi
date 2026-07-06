# pyrefly: ignore [missing-import]
from sigzenbi_client.www.register.register import render_signup


def get_context(context):
    # Thin wrapper: the registered-guard + mirror-render logic lives in render_signup
    # (register/register.py), reused verbatim so /portal/signup and the retired
    # /register/register never diverge.
    return render_signup(context)
