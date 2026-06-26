# Decisions

A filled-in example so you can see what a healthy decision log looks like for a
small app. Yours will grow one entry at a time as you build. (Starter template:
`../templates/DECISIONS.md`.)

---

## D-0001 — Adopt a decision log
Date: 2026-06-20  ·  Status: Accepted

Context: Decisions lived only in chat history and got re-argued.
Decision: Keep this append-only `DECISIONS.md`; read before non-trivial work.
Alternatives: PR descriptions only (rejected: not discoverable later).
Consequences: Substantive changes cite or add an entry here.

## D-0002 — Use Supabase for auth and database
Date: 2026-06-21  ·  Status: Accepted

Context: Needed login + a database without running our own server.
Decision: Supabase (Postgres + built-in auth).
Alternatives: Firebase (rejected: prefer SQL); roll our own (rejected: too much
to maintain for a solo build).
Consequences: All data access goes through the Supabase client. Secrets live in
env vars, never in code.

## D-0003 — Auth uses magic-link (email) sign-in
Date: 2026-06-22  ·  Status: Accepted

Context: Wanted the simplest secure login for non-technical users.
Decision: Passwordless magic links via Supabase auth.
Alternatives: Email+password (rejected: password reset flows + storage burden);
social login (deferred: add later if users ask).
Consequences: One sign-in path. New screens that need a logged-in user reuse the
existing session helper — do not add a second auth flow.

## D-0009 — Login screen reuses the magic-link flow
Date: 2026-06-26  ·  Status: Accepted

Context: Adding a dedicated login screen.
Decision: The screen calls the existing magic-link helper from D-0003.
Alternatives: A new email+password form (rejected: would create a second,
conflicting auth path — see D-0003).
Consequences: Single source of truth for auth stays intact.
