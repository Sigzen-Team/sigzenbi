"""The one click that puts an analyst inside the analytics tool, already signed in.

WHY THIS ENDPOINT IS ON THE CLIENT BOX AND NOT ON CENTRAL
Central is subscription management and is never a customer-facing surface (founder, 2026-08-08).
That is exactly what killed the OAuth SSO this replaces: authorization-code flow *requires* the
browser to visit the identity provider, so every analyst login walked the customer through
the SigzenBI hub. Here the browser makes two hops it is allowed to make — the client
domain, then the analytics domain — and the Central conversation happens server-to-server, out of
sight, over the session this box already holds.

WHY IT FORWARDS THE SID AND ASSERTS NOTHING
Only the browser's `central_sid` cookie is sent, and no Authorization header, so Central resolves
the person from their own session and applies its own analyst gate. This box never says who the
visitor is. Using `utils.call_central_api` here instead would authenticate as the org owner (see
team_proxy's HARD RULE) — on a login endpoint that is not a proxy bug, it is a "every member is
silently the owner in the analytics tool" bug.

IDENTITY IS RESOLVED LIVE, NEVER READ FROM THE COOKIE (founder: "don't take from cache, it
should be real", 2026-08-09) — AND THAT IS A SECURITY RULE, NOT A FRESHNESS ONE.
This endpoint used to read `central_sid` straight off the request. `resolve_bi_user` exists
precisely to stop that: *"a LIVE ERP session wins over a stale client_session_user cookie ...
fixes the stale-cookie identity bleed where switching ERP accounts in one browser kept the
previous BI session"*. Bypassing it reintroduced the bleed on the one endpoint where it grants a
LOGIN. Measured: sign in as an analyst, switch the ERP session to a viewer, click
Open Analytics -> **signed into Superset as the analyst**. A viewer inheriting an analyst's analytics
session, from a cookie. Now the live session decides, and `resolve_bi_user` fails CLOSED when the
ERP user is not a vouchable member rather than falling back to whoever the cookie names.

WHY IT RE-VOUCHES (founder: "SSO to superset is not working sometimes", 2026-08-09)
A `central_sid` cookie OUTLIVES the Central session it names. Session expiry, an explicit logout,
or anything that drops the session leaves the cookie sitting in the browser looking perfectly
good. The portal PAGE survives that because `central_get_with_sid` re-vouches once on 401/403 —
this endpoint used bare `_forward`, which does not, so it inherited exactly the bug that helper
was written to fix.

The symptom is why it read as random: the dashboard keeps rendering (it re-vouched), so the
customer is certain they are signed in, and only "Open Analytics" quietly bounces them back to
the dashboard. Reproduced by killing the Central session and clicking: dashboard renders,
hand-off does not reach Superset.

WHY A GET WITH A SIDE EFFECT IS FINE HERE
It mints a credential for the caller's own session and hands it to the caller's own browser. A
forged cross-origin GET produces a redirect the attacker cannot read and a token that logs the
victim in as themselves — nothing to steal, no state to change. This is the ordinary shape of an
SSO initiation link, and it is what lets the button be a plain `<a href>` that survives popup
blockers, middle-click and "open in new tab".
"""
import frappe
import requests


def _central_base():
    base = frappe.db.get_single_value("SigzenBI Subscription Settings", "sigzenbi_erp_link") or ""
    if base and not base.endswith("/"):
        base += "/"
    return base


def _mint_with_sid(central_sid, next=None):
    """Ask Central for a hand-off URL, carrying ONLY this sid. None if the session is not good.

    Deliberately not `team_proxy._forward`: this needs to be callable with a sid that is not the
    one on the incoming request (the re-vouched one), and `_forward` reads the cookie itself.
    The rule that matters is preserved verbatim — sid cookie only, never an Authorization header,
    so a dead session fails closed as Guest instead of silently acting as the org owner.
    """
    base = _central_base()
    if not (base and central_sid):
        return None
    try:
        resolve = requests.get(
            f"{base}api/method/sigzenbi_central.www.client_login.resolve_session_user",
            cookies={"sid": central_sid}, timeout=10)
        msg = (resolve.json() or {}).get("message") if resolve.status_code == 200 else None
        user = msg.get("user") if isinstance(msg, dict) else None
        csrf = (msg.get("csrf_token") if isinstance(msg, dict) else "") or ""
        if not user or user == "Guest":
            return None
        r = requests.post(
            f"{base}api/method/sigzenbi_central.API.team.superset_identity"
            f".mint_analytics_handoff",
            json=({"next": next} if next else {}), cookies={"sid": central_sid},
            headers={"X-Frappe-CSRF-Token": csrf} if csrf else {}, timeout=15)
        if not r.ok:
            return None
        return ((r.json() or {}).get("message") or {}).get("url")
    except Exception:
        # No token, no sid, no secret in the log — only that the mint did not happen.
        frappe.log_error(title="analytics handoff",
                         message="mint_analytics_handoff failed (see traceback)\n\n"
                                 + frappe.get_traceback())
        return None


@frappe.whitelist(allow_guest=True)
def open_analytics(next=None):
    """Mint a hand-off token at Central and send the browser straight to the analytics domain.

    The token is in the URL for exactly one hop and is consumed server-side on arrival (~60 s TTL,
    single use). Nothing here logs it, and it is never written to this box's storage.

    Any failure — viewer seat, Central unreachable, a session that cannot be re-established — lands
    the person back in the portal rather than on a Frappe traceback page. The reason is
    deliberately not echoed into the URL: "you are not an analyst" and "your session expired" are
    the same non-answer to anyone probing this endpoint.
    """
    url = None
    try:
        # THE ONE IDENTITY RESOLVER, not the cookie. A live ERP session outranks the BI cookie and
        # is re-vouched when they differ; a differing, non-vouchable ERP user returns (None, None)
        # and drops the stale cookie rather than serving someone else's session. Everything this
        # endpoint grants rests on that answer being right.
        from sigzenbi_client.utils import resolve_bi_user

        central_sid, client_user = resolve_bi_user()
        if client_user:
            url = _mint_with_sid(central_sid, next)
            if not url:
                # The sid resolved to a member but Central no longer knows the session — the
                # cookie outlived it (expiry, logout, anything that drops it). The portal PAGE
                # hides this by re-vouching once, which is why only this button appeared to fail
                # "sometimes". Re-vouch for the SAME person and retry.
                from sigzenbi_client.www.client_dashboard import _vouch_for_logged_in_user

                visitor = getattr(frappe.session, "user", None)
                if visitor and visitor != "Guest":
                    new_sid, _user = _vouch_for_logged_in_user(visitor)
                    if new_sid:
                        url = _mint_with_sid(new_sid, next)
    except Exception:
        frappe.log_error(title="analytics handoff",
                         message="open_analytics failed\n\n" + frappe.get_traceback())

    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = url or "/client_dashboard"
