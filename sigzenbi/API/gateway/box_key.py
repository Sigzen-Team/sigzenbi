"""This box's own key pair (Central SRS Part F): credential delivery is locked to this box.

* Generated on install / every migrate if missing: an Ed25519 signing key and an X25519
  encryption key, in `sites/<site>/private/sigzenbi_box.key`, mode 0600. Outside the database
  and outside `private/files`, so neither a DB dump nor `bench backup --with-files` carries it.
  Never logged, never sent anywhere: only the PUBLIC halves go to Central.
* Enrolled on Central by the poll loop: retried until Central confirms it, re-confirmed every
  RECHECK_S, and redone at once when Central answers a fetch unsealed or refuses it (a "Reset
  Box Key" in Central Desk). Enroll carries the gateway secret AND this tenant's api pair as
  token auth. Central keeps the first key it sees; a rebuilt box needs Sigzen to press "Reset
  Box Key" on the Client User in Central Desk.
* Every sealed reply is bound (AEAD associated data) to the signed request that asked for it.
* Once enrolled, Central only hands this box its api pair (and a rotated gateway secret) for a
  request signed with this key, sealed so only this key can open it.

`seal`/`open_sealed`/`signed_message` are an identical copy of Central's
`sigzenbi_central/API/gateway/box_key.py`. Change both or neither.
"""
import base64
import json
import os
import secrets
import time

import frappe
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

KEY_FILE = "sigzenbi_box.key"
TS_HEADER = "X-Sigzen-Box-Ts"
NONCE_HEADER = "X-Sigzen-Box-Nonce"
SIG_HEADER = "X-Sigzen-Box-Sig"
FETCH = "fetch_current_api_credentials"
ROTATE = "rotate_gateway_secret"
_SEAL_INFO = b"sigzenbi-seal-v1"
_SIG_DOMAIN = "sigzenbi-box-v1"
_GATEWAY = "sigzenbi_central.API.gateway"
SIG_WINDOW_S = 60     # Central's signature window
RECHECK_S = 600       # re-confirm enrollment this often (catches a Reset Box Key on Central)
RETRY_S = 60          # retry a failed enroll this often
_enrolled = {}        # client_name -> (confirmed, monotonic time of the last attempt)


# ---- primitives (identical copy on Central) -----------------------------------------
def b64(raw):
    return base64.b64encode(raw).decode()


def raw_public(private_key):
    return private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _raw_private(private_key):
    return private_key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                     serialization.NoEncryption())


def signed_message(purpose, client_name, ts, nonce):
    return "\n".join([_SIG_DOMAIN, purpose, client_name or "", str(ts), nonce or ""]).encode()


def enroll_purpose(sign_pubkey, enc_pubkey):
    return f"enroll:{sign_pubkey}:{enc_pubkey}"


def _seal_key(shared, eph_pub, recipient_pub):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=eph_pub + recipient_pub,
                info=_SEAL_INFO).derive(shared)


def open_sealed(private_key, sealed_b64, aad=None):
    blob = base64.b64decode(sealed_b64)
    eph_pub, nonce, ct = blob[:32], blob[32:44], blob[44:]
    key = _seal_key(private_key.exchange(X25519PublicKey.from_public_bytes(eph_pub)), eph_pub,
                    raw_public(private_key))
    return ChaCha20Poly1305(key).decrypt(nonce, ct, aad)


# ---- the key file -------------------------------------------------------------------
def key_path():
    return frappe.get_site_path("private", KEY_FILE)


def _create_key_file(path):
    """Write a complete 0600 temp file, then hard-link it into place: the link is atomic and
    fails if the file exists, so no reader ever sees a half-written key and two first starts
    cannot both win."""
    sign, enc = Ed25519PrivateKey.generate(), X25519PrivateKey.generate()
    data = json.dumps({"sign": b64(_raw_private(sign)), "enc": b64(_raw_private(enc))}).encode()
    tmp = f"{path}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp, path)
        frappe.logger("sigzen_gateway").info("box key pair generated")
    except FileExistsError:
        pass   # another process won the race; read its key
    finally:
        os.unlink(tmp)


def ensure_box_key():
    """Return (signing key, encryption key), creating the 0600 key file on first call."""
    path = key_path()
    if not os.path.exists(path):
        _create_key_file(path)
    try:
        if os.stat(path).st_mode & 0o077:
            os.chmod(path, 0o600)
        with open(path) as f:
            data = json.load(f)
        return (Ed25519PrivateKey.from_private_bytes(base64.b64decode(data["sign"])),
                X25519PrivateKey.from_private_bytes(base64.b64decode(data["enc"])))
    except Exception as e:
        # Loud, once per caller, and never the file's content. A corrupt file is NOT replaced
        # on its own: a new key would lock this box out of Central (needs a "Reset Box Key").
        frappe.log_error(title="SigzenBI box key file unreadable",
                         message=f"{path}: {type(e).__name__}. Fix the file's owner/mode, or move it "
                                 "aside and ask Sigzen to Reset Box Key for this organisation.")
        raise


def public_keys():
    sign, enc = ensure_box_key()
    return b64(raw_public(sign)), b64(raw_public(enc))


def signed_headers(purpose, client_name):
    sign, _ = ensure_box_key()
    ts, nonce = str(int(time.time())), secrets.token_hex(16)
    return {TS_HEADER: ts, NONCE_HEADER: nonce,
            SIG_HEADER: b64(sign.sign(signed_message(purpose, client_name, ts, nonce)))}


def request_aad(purpose, client_name, headers):
    """What Central sealed its reply against: the signed request itself."""
    return signed_message(purpose, client_name, headers[TS_HEADER], headers[NONCE_HEADER])


def unseal_json(sealed_b64, aad=None):
    _, enc = ensure_box_key()
    return json.loads(open_sealed(enc, sealed_b64, aad))


def refusal_reason(resp):
    """HTTP status, plus 'clock skew' when this box's clock is off Central's by more than the
    signature window (Central then refuses every signed call with a bare 401)."""
    from email.utils import parsedate_to_datetime

    why = f"HTTP {resp.status_code}"
    try:
        skew = time.time() - parsedate_to_datetime(resp.headers["Date"]).timestamp()
        if abs(skew) > SIG_WINDOW_S - 5:
            why += f" (clock skew {int(skew)} s vs Central: fix NTP on this box)"
    except Exception:
        pass
    return why


# ---- calls to Central ---------------------------------------------------------------
def _post(method, client_name, purpose, data=None, extra_headers=None):
    """Returns (message, signed headers) or (None, None)."""
    import requests

    from sigzenbi.API.gateway.poll_jobs import GATEWAY_SECRET_HEADER, _central_url, _secret

    secret = _secret(client_name)
    if not (_central_url() and client_name and secret):
        return None, None
    signed = signed_headers(purpose, client_name)
    resp = requests.post(f"{_central_url()}/api/method/{_GATEWAY}.box_key.{method}",
                         data={"client_name": client_name, **(data or {})},
                         headers={GATEWAY_SECRET_HEADER: secret, **signed, **(extra_headers or {})},
                         timeout=15)
    if resp.status_code != 200:
        # Status only: never a URL, header or body near a log line.
        frappe.log_error(title=f"SigzenBI box key: {method} refused",
                         message=f"client_name: {client_name}\n{refusal_reason(resp)}")
        return None, None
    return resp.json().get("message") or {}, signed


def enroll(client_name):
    """Send this box's public keys to Central (no-op there once the same key is enrolled).
    Central also wants this tenant's current api pair, so a stolen gateway secret alone
    cannot enroll a key."""
    from sigzenbi import credentials as client_credentials

    creds = client_credentials.get_credentials(client_name)
    if not creds:
        return False
    sign_pub, enc_pub = public_keys()
    res, _ = _post("enroll_box_key", client_name, enroll_purpose(sign_pub, enc_pub),
                   {"sign_pubkey": sign_pub, "enc_pubkey": enc_pub},
                   {"Authorization": f"token {creds['key']}:{creds['secret']}"})
    return bool(res and res.get("enrolled"))


def ensure_enrolled(client_name):
    """Called by the poll loop every cycle; talks to Central only when due. Never raises."""
    confirmed, at = _enrolled.get(client_name, (False, None))
    if at is not None and time.monotonic() - at < (RECHECK_S if confirmed else RETRY_S):
        return confirmed
    try:
        confirmed = enroll(client_name)
    except Exception:
        confirmed = False
        frappe.log_error(title="SigzenBI box key enroll failed", message=frappe.get_traceback())
    _enrolled[client_name] = (confirmed, time.monotonic())
    return confirmed


def forget_enrollment(client_name):
    """Central no longer treats this box as enrolled (reset, or never was): enroll next cycle."""
    _enrolled.pop(client_name, None)


def rotate_gateway_secret(client_name=None):
    """Ops: `bench --site <site> execute sigzenbi.API.gateway.box_key.rotate_gateway_secret
    --kwargs '{"client_name": "<name>"}'`. Gets a new gateway secret from Central (signed,
    sealed), saves it, then uses it once (a signed credential fetch) so Central switches over.
    Until that use, Central still accepts the old secret, so a failed run can just be repeated."""
    from sigzenbi import credentials as client_credentials
    from sigzenbi.API.gateway.poll_jobs import _client_name, refresh_credentials_from_central

    client_name = client_name or _client_name()
    res, signed = _post("rotate_gateway_secret", client_name, ROTATE)
    if not (res and res.get("sealed")):
        return {"rotated": False}
    new = unseal_json(res["sealed"], request_aad(ROTATE, client_name, signed))["gateway_secret"]
    client_credentials.set_gateway_secret(client_name, new)
    confirmed = refresh_credentials_from_central(client_name)
    frappe.logger("sigzen_gateway").info(f"'{client_name}': gateway secret rotated (confirmed={confirmed})")
    return {"rotated": True, "confirmed": confirmed}
