# What has been reviewed

This list says what people outside the project have reviewed, and what nobody has. Nothing here is a security
audit. No paid or formal audit has been done.

| Date | Who | What they looked at | What they found | Result |
|---|---|---|---|---|
| 2026-10 | krivonosoff161 | Explicit keys, approvals, argument comparison ([issue #1](https://github.com/salahmakboul/call4agents/issues/1)) | A reused key returned a stale result; an approval could cover other arguments after a crash ([GHSA-gxg5-95g9-xw4w](https://github.com/salahmakboul/call4agents/security/advisories/GHSA-gxg5-95g9-xw4w)); `Decimal("10.0")` and `"10.0"` share a signature | First two fixed in 0.5.0 and 0.5.2. Third confirmed, still open |

## Not reviewed yet

- Approval tickets (`issue_ticket`, `decide_ticket` in `call4agents/ledger.py`): a review has been requested.
- Replay (`call4agents/replay.py`): a review has been requested.
- The PostgreSQL shared record (beta).
- The framework bridges.
- `call4agents crashtest`, `scan` and `audit` (experimental).

If you review any of this, please send what you find through [SECURITY.md](../SECURITY.md) if it is a
vulnerability, or as an issue if it is not. You'll be added here, with your name or without it, as you prefer.
