# pyrefly: ignore [missing-import]
from sigzenbi_client.www.client_login import render_bi_login

# NEVER CACHE THIS PAGE. Frappe caches rendered www pages on path+language only --
# no user -- so a cached copy is served to EVERYONE. This page delegates to client_login.render_bi_login, whose module-level no_cache does NOT
# carry across to this route -- the renderer reads the flag off THIS module.
# Module level, not context.no_cache: the renderer reads it off the module, so it
# still applies on a path that returns or redirects early.
no_cache = True

def get_context(context):
    # Thin wrapper: the audited approach-C resolution order lives in render_bi_login
    # (client_login.py), reused verbatim so /portal/login and the retired /client_login
    # never diverge. Never inline auth logic here.
    return render_bi_login(context)
