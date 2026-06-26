# Decisions

Append-only log of non-trivial decisions for this repo. Newest at the bottom.
Read before starting architectural/data/auth/dependency/interface work; add an
entry whenever you choose between real alternatives. Supersede, don't delete.

Format per entry:

```
## D-NNNN — <one-line decision>
Date: YYYY-MM-DD  ·  Status: Accepted | Superseded by D-NNNN | Deprecated

Context: <what problem / why a decision was needed>
Decision: <what was chosen>
Alternatives: <options considered and why rejected>
Consequences: <follow-on obligations, migrations, things to keep in sync>
```

---

## D-0001 — Adopt a decision log
Date: 2026-06-26  ·  Status: Accepted

Context: Decisions were living only in people's heads and in diffs, so they got
re-litigated and silently contradicted.
Decision: Maintain this append-only `DECISIONS.md`; read it before non-trivial
work and record decisions worth remembering.
Alternatives: Scatter rationale in PR descriptions (rejected: not discoverable
later); rely on code comments (rejected: no cross-cutting view).
Consequences: Each substantive change should cite or add a decision here.
