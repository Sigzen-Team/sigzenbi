"""
Idempotent agent self-registration, invoked by install_agent.sh Step 2/3.

Deliberately reuses the EXISTING self-registration path
(sigzenbi_client.www.register.register.get_client_credentials, C6-guarded on
Central) rather than re-implementing the Central POST/parse/credential-store
dance here. Credential storage (per-client_name, encrypted) is entirely owned
by sigzenbi_client.credentials — this module never touches
`SigzenBI Client Credential` directly and never logs a secret.
"""
import frappe


def run(central_url=None, **registration_kwargs):
    settings = frappe.get_single("SigzenBI Subscription Settings")

    # Step 2: point this site at Central. Idempotent — only writes if it actually
    # changes (the code that reads this is sigzenbi_erp_link, NOT a site_config key;
    # install_agent.sh's `set-config sigzenbi_central_url` is informational only).
    if central_url:
        central_url = central_url.rstrip("/")
        current = (settings.sigzenbi_erp_link or "").rstrip("/")
        if current != central_url:
            settings.sigzenbi_erp_link = central_url
            settings.save(ignore_permissions=True)
            frappe.db.commit()
            print(f"[register_agent] Central URL set to {central_url}")
        else:
            print(f"[register_agent] Central URL already {central_url} (no-op).")

    # Step 3: self-registration. Already registered (client_name set on the
    # singleton, per-client_name credential store from -> clean no-op.
    client_name = settings.client_name
    if client_name:
        from sigzenbi_client import credentials as client_credentials

        has_central_creds = client_credentials.get_credentials(client_name) is not None
        has_gateway_secret = bool(client_credentials.get_gateway_secret(client_name))
        print(
            f"[register_agent] PASS: already registered as '{client_name}' "
            f"(central_api_credentials={'present' if has_central_creds else 'MISSING'}, "
            f"gateway_secret={'present' if has_gateway_secret else 'MISSING'})."
        )
        return {"status": "ok", "client_name": client_name, "already_registered": True}

    # Not registered yet. The existing self-registration endpoint needs real
    # registration inputs (email/password/...); without them, degrade to a WARN
    # instead of failing the whole install — this box may be pending
    # onboarding, matching the plan's "still installs, logs that it's pending".
    if not {"email", "password"}.issubset(registration_kwargs):
        print(
            "[register_agent] WARN: not registered yet and no --email/--password "
            "supplied — skipping registration (idempotent no-op). Re-run with "
            "registration flags once you have signup credentials."
        )
        return {"status": "warn", "already_registered": False}

    from sigzenbi_client.www.register.register import get_client_credentials

    result = get_client_credentials(**registration_kwargs)
    if result.get("status") == "success":
        print(f"[register_agent] Registered as '{result.get('client_name')}'.")
        return {"status": "ok", "client_name": result.get("client_name"), "already_registered": False}

    print(f"[register_agent] WARN: registration failed: {result.get('message')}")
    return {"status": "warn", "error": result.get("message"), "already_registered": False}
