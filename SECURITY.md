# Security policy

Call4Agents guards real-world actions (payments, emails, approvals), so a bug in it can be a security bug.
Please report those privately.

## How to report

Use GitHub's private reporting: **Security → Report a vulnerability** on this repository
(https://github.com/salahmakboul/call4agents/security/advisories/new). Only the maintainers see it.

Please include the version (`call4agents doctor` prints it), what you did, what happened, and what you
expected. A small script that shows it is the most helpful thing you can send.

Please do not open a public issue for a vulnerability, and do not test against systems that are not yours.

## What happens next

- We answer every report within **24 hours**.
- We fix serious reports within **7 days**: anything in the scope list below that can run an action twice, run
  it without its approval, or leak a secret.
- We fix it in a new release, publish a GitHub security advisory, and credit you unless you prefer not.
- If a release turns out to be unsafe, we yank it on PyPI (see [docs/RELEASING.md](docs/RELEASING.md)).

If we cannot fix something in 7 days, we tell you why within those 7 days and agree a date with you.

The contact is the private reporting form above. See [docs/REVIEWED.md](docs/REVIEWED.md) for what outside
people have reviewed so far.

## Supported versions

Only the latest release on PyPI gets security fixes. The project is at version 0.x: upgrade to the latest
version to get a fix.

## What is in scope

- An approval that can be used for other arguments, a second time, or after a "no".
- An approval ticket that is accepted although this record did not issue it, or it was changed, expired or
  already decided.
- An action that runs twice when the record says it ran once, or a record that silently loses entries.
- Secrets or personal data written somewhere they should not be.

What the kit does not protect against is described in [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md), for
example someone who can read the secret file. Reports that show the threat model is wrong are welcome too.
