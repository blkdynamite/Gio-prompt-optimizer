# Security

## What Gio touches

- Gio runs locally. Its scripts read your project files and Claude Code's own
  usage logs under `~/.claude/`. Nothing is uploaded; there is no telemetry.
- The only network access is optional: a one-time embedding model download at
  index time if you install the semantic extras, and `git clone` of two pinned
  public repos when you run the retrieval eval. The default lexical path makes
  no network calls.
- The landing page under `site/` is static. Its notify-me form posts an email
  address to Klaviyo's public subscribe endpoint and nothing else.

## Reporting a vulnerability

Please report privately through GitHub's security advisories for this
repository ("Report a vulnerability" under the Security tab) rather than a
public issue. Include steps to reproduce. You will get an acknowledgement
within a week.
