"""
Per-client_name Central API credential storage.

This site hosts many `client_name` identities (see CLAUDE.md — one bench,
many client_names), but until this module existed, all of them shared a
single `SigzenBI Subscription Settings` singleton row for Central API
credentials — any identity's rotation clobbered every other identity's key,
causing intermittent/persistent 401s (the credential-rotation race).

This module is the SOLE reader/writer of the `SigzenBI Client Credential`
doctype, which stores one row per `client_name`. All credential reads/writes
for Central API calls should go through `get_credentials()` /
`save_rotated()` / `upsert_root()` here rather than touching the doctype
directly.

Like `utils.py::update_subscription_credentials()` (the pattern this mirrors),
writes use raw `frappe.db.set_value` / `frappe.db.exists` + `set_encrypted_password`
instead of `doc.save()` — this avoids TimestampMismatchError when multiple
concurrent poll loops/requests for the same client_name race to persist a
rotated credential.

Never logs api_key/api_secret values anywhere in this module.
"""
import frappe
from frappe.utils.password import get_decrypted_password, set_encrypted_password

DOCTYPE = "SigzenBI Client Credential"


def get_credentials(client_name):
    """
    Return {"key": ..., "secret": ..., "source": "doctype"|"singleton"} for
    the given client_name, or None if nothing is available at all.

    Prefers the doctype row's central_api_key/central_api_secret (the most
    recently rotated pair). Falls back to the doctype row's api_key/api_secret
    (the stable "root" pair) if no rotated pair has been recorded yet. Falls
    back further to the shared SigzenBI Subscription Settings singleton
    (central_api_key/central_api_secret, else api_key/api_secret) for
    backward-compat while sites are mid-migration to per-client_name rows.

    Never logs secret values.
    """
    if not client_name:
        return None

    if frappe.db.exists(DOCTYPE, client_name):
        central_api_key = frappe.db.get_value(DOCTYPE, client_name, "central_api_key")
        if central_api_key:
            central_api_secret = get_decrypted_password(
                DOCTYPE, client_name, "central_api_secret", raise_exception=False
            )
            if central_api_secret:
                return {"key": central_api_key, "secret": central_api_secret, "source": "doctype"}

        api_key = frappe.db.get_value(DOCTYPE, client_name, "api_key")
        if api_key:
            api_secret = get_decrypted_password(DOCTYPE, client_name, "api_secret", raise_exception=False)
            if api_secret:
                return {"key": api_key, "secret": api_secret, "source": "doctype"}

    # Backward-compat fallback: the shared singleton, during migration.
    settings_central_key = frappe.db.get_single_value("SigzenBI Subscription Settings", "central_api_key")
    if settings_central_key:
        settings_central_secret = get_decrypted_password(
            "SigzenBI Subscription Settings", "SigzenBI Subscription Settings", "central_api_secret", raise_exception=False
        )
        if settings_central_secret:
            return {"key": settings_central_key, "secret": settings_central_secret, "source": "singleton"}

    settings_api_key = frappe.db.get_single_value("SigzenBI Subscription Settings", "api_key")
    if settings_api_key:
        settings_api_secret = get_decrypted_password(
            "SigzenBI Subscription Settings", "SigzenBI Subscription Settings", "api_secret", raise_exception=False
        )
        if settings_api_secret:
            return {"key": settings_api_key, "secret": settings_api_secret, "source": "singleton"}

    return None


def save_rotated(client_name, next_key, next_secret):
    """
    Upsert the SigzenBI Client Credential row for client_name: set
    central_api_key/central_api_secret (encrypted) to the freshly rotated
    pair, last_rotated=now, last_source="rotation".

    Uses frappe.db.exists + frappe.new_doc/frappe.db.set_value rather than
    doc.save() so concurrent pollers rotating the same client_name's
    credentials don't hit TimestampMismatchError.
    """
    if not client_name:
        return

    if not frappe.db.exists(DOCTYPE, client_name):
        doc = frappe.new_doc(DOCTYPE)
        doc.client_name = client_name
        doc.insert(ignore_permissions=True)

    frappe.db.set_value(DOCTYPE, client_name, "central_api_key", next_key)
    set_encrypted_password(DOCTYPE, client_name, next_secret, "central_api_secret")
    frappe.db.set_value(DOCTYPE, client_name, "last_rotated", frappe.utils.now())
    frappe.db.set_value(DOCTYPE, client_name, "last_source", "rotation")

    frappe.db.commit()


def upsert_root(client_name, api_key, api_secret, source):
    """
    Upsert the SigzenBI Client Credential row for client_name: set BOTH
    api_key/api_secret AND central_api_key/central_api_secret (a fresh pair
    is current by definition), last_rotated=now, last_source=source.

    Uses raw frappe.db.set_value / set_encrypted_password rather than
    doc.save() for the same concurrency reasons as save_rotated().
    """
    if not client_name:
        return

    if not frappe.db.exists(DOCTYPE, client_name):
        doc = frappe.new_doc(DOCTYPE)
        doc.client_name = client_name
        doc.insert(ignore_permissions=True)

    frappe.db.set_value(DOCTYPE, client_name, "api_key", api_key)
    set_encrypted_password(DOCTYPE, client_name, api_secret, "api_secret")
    frappe.db.set_value(DOCTYPE, client_name, "central_api_key", api_key)
    set_encrypted_password(DOCTYPE, client_name, api_secret, "central_api_secret")
    frappe.db.set_value(DOCTYPE, client_name, "last_rotated", frappe.utils.now())
    frappe.db.set_value(DOCTYPE, client_name, "last_source", source)

    frappe.db.commit()


def set_gateway_secret(client_name, secret):
    """
    Upsert the per-client_name transport secret this identity's polling agent
    uses to authenticate to Central's gateway endpoints (pending_query,
    submit_query_result, heartbeat). Stored encrypted, one row per client_name
    — the per-tenant replacement for the shared gateway_shared_secret.

    Uses raw frappe.db.exists + set_encrypted_password (no doc.save()) for the
    same concurrency reasons as save_rotated()/upsert_root(). Never logs the secret.
    """
    if not client_name or not secret:
        return

    if not frappe.db.exists(DOCTYPE, client_name):
        doc = frappe.new_doc(DOCTYPE)
        doc.client_name = client_name
        doc.insert(ignore_permissions=True)

    set_encrypted_password(DOCTYPE, client_name, secret, "gateway_secret")
    frappe.db.commit()


def get_gateway_secret(client_name):
    """
    Return this client_name's per-tenant gateway transport secret. Falls back to
    the shared singleton (site_config `sigzen_gateway_shared_secret`) while sites
    are mid-migration to per-client_name rows. Never logs the secret.
    """
    if client_name and frappe.db.exists(DOCTYPE, client_name):
        val = get_decrypted_password(DOCTYPE, client_name, "gateway_secret", raise_exception=False)
        if val:
            return val
    return frappe.conf.get("sigzen_gateway_shared_secret")


def get_api_secrets_all(client_name):
    """Return BOTH of this client_name's api secrets — the rotated `central_api_secret` and the
    stable root `api_secret` — de-duped and non-null. The bootstrap endpoints
    (fetch_first_user/receive_secret) authenticate against these, and must accept whichever one
    Central happens to sign with (root vs current can differ across a rotation). Falls back to
    get_credentials() (singleton-aware) if this bench has no per-client_name row yet. Never logs.
    """
    out = []
    if client_name and frappe.db.exists(DOCTYPE, client_name):
        for field in ("central_api_secret", "api_secret"):
            v = get_decrypted_password(DOCTYPE, client_name, field, raise_exception=False)
            if v and v not in out:
                out.append(v)
    if not out:
        creds = get_credentials(client_name)
        if creds and creds.get("secret"):
            out.append(creds["secret"])
    return out


def get_gateway_secret_strict(client_name):
    """
    Return this client_name's OWN stored per-tenant gateway secret, WITHOUT the
    global singleton fallback that get_gateway_secret() applies. Returns None if no
    per-tenant row/value exists yet. Inbound auth uses this to tell a REAL per-tenant
    secret apart from the legacy global during the C3-completion migration (so it can
    accept the global only behind the transition flag, never conflate the two).
    Never logs the secret.
    """
    if client_name and frappe.db.exists(DOCTYPE, client_name):
        return get_decrypted_password(DOCTYPE, client_name, "gateway_secret", raise_exception=False) or None
    return None
