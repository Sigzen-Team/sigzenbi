# pyrefly: ignore [missing-import]
from sigzenbi_client.www.register.register import render_signup

# NEVER CACHE THIS PAGE. Frappe caches rendered www pages on path+language only --
# no user -- so a cached copy is served to EVERYONE. This page delegates to register.render_signup, whose module-level no_cache does NOT carry
# across to this route -- the renderer reads the flag off THIS module.
# Module level, not context.no_cache: the renderer reads it off the module, so it
# still applies on a path that returns or redirects early.
no_cache = True

def get_context(context):
    # Thin wrapper: the registered-guard + mirror-render logic lives in render_signup
    # (register/register.py), reused verbatim so /portal/signup and the retired
    # /register/register never diverge.
    return render_signup(context)
