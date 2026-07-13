# sigzenbi_client

The authoritative docs live next to the code at **`sigzenbi_client/CLAUDE.md`** and its subsystem docs
(`sigzenbi_client/API/gateway/CLAUDE.md`, `sigzenbi_client/docs/SECURITY.md`).

Do not add content here. This file used to be a full copy that **drifted into a stale near-duplicate** —
it was missing the 2026-07-04 security round (the reverted `register.py` hijack guard, the `sigzen_ro`
read-only DB user, per-tenant gateway secrets), which is exactly the kind of silent divergence that gets
someone to trust a guard that isn't there. It's now a pointer so navigation from the app root still finds
the real docs, with a single source of truth below it.
