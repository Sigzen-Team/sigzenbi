"""Client half of member-scope cache invalidation (SPEC-member-row-security §3.2).

Central caches a member's per-doctype row clause for 60 s (the TTL backstop). The doc events
wired in hooks.py are the same set Frappe itself invalidates permissions on — User Permission,
DocShare, User, DocPerm / Custom DocPerm, Server Script, System Settings. POSTing Central's
`bust_member_scope` on those makes an admin's permission edit bite on the NEXT dashboard
render instead of up to a minute later.

Fire-and-forget by design, in both halves:
- the doc event only ENQUEUES (after commit) — a user's save must never wait on, or fail
  because of, a network call to Central;
- the worker posts once per locally-hosted client_name identity with that tenant's own
  gateway_secret (the same per-tenant transport every other client→Central call uses), and
  swallows every failure. Over-busting costs one cheap re-read; a failed bust degrades to
  the 60 s TTL — never fail-open, never a broken save.

Never logs a secret or a URL.
"""
import frappe

_JOB = "sigzenbi_client.API.gateway.bust_scope.bust_central_member_scope"


def on_permission_change(doc, method=None):
    """hooks.py doc_events target. Enqueue-only; never raises into the caller's save."""
    try:
        frappe.enqueue(_JOB, queue="short", enqueue_after_commit=True)
    except Exception:
        # Redis/queue down: the 60 s TTL still bounds staleness. Breaking the save that
        # GRANTED/REVOKED the permission would be strictly worse than a stale minute.
        pass


def bust_central_member_scope():
    """Worker: tell Central to drop every cached clause for every identity this bench hosts.

    Whole-tenant busts (no member narrowing) on purpose — mirrors bust_member_scope's own
    contract: narrowing on a guessed member could UNDER-invalidate, which is the direction
    that serves revoked permission.
    """
    import requests

    from sigzenbi_client.API.gateway.poll_jobs import (
        _candidate_client_names,
        _central_url,
        _secret,
    )

    base = _central_url()
    if not base:
        return
    for name in _candidate_client_names():
        try:
            secret = _secret(name)
            if not secret:
                continue
            requests.post(
                f"{base}/api/method/"
                "sigzenbi_central.API.team.member_scope_cache.bust_member_scope",
                json={"client_name": name, "secret": secret},
                timeout=10,
            )
        except Exception:
            # Type-free, detail-free: the message could echo the URL. TTL is the backstop.
            frappe.logger().warning("member-scope bust failed for one identity")
