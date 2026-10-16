# Security Policy

## Supported version

Trace Case is still in pre-production validation. Security fixes are applied only to the latest commit on
`main`; no released production version or long-term support branch exists yet.

The application intentionally has no built-in login or role-based access control. Deploy Web and API only
inside an approved private network or VPN. Expose only the minimum Slack webhook ingress through TLS.

## Report a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/kimdol1045-hash/trace-case/security/advisories/new).
Include the affected component, reproduction steps, expected impact, and a minimal proof of concept.

Do not open a public issue with a credential, customer record, internal URL, production log, or
unredacted screenshot. Do not test against a production or third-party system without explicit permission.

The maintainer will acknowledge a report when it is reviewed, coordinate a private fix, and publish an
advisory when disclosure is safe. Because this is a pre-production project, response times are best effort.

## Repository safeguards

- GitHub secret scanning and push protection guard known provider credential formats.
- The `Security` workflow audits JavaScript and Python dependencies, runs Bandit on production Python
  sources, runs CodeQL, and rejects unapproved credential-shaped literals, personal paths, and source
  exports in tracked files.
- Dependabot checks package and workflow dependencies on a schedule.
- Runtime credentials belong in an approved secret store or deployment environment, never in Git history.

If any secret is exposed, revoke it first. Removing it from the current file is not sufficient because Git
history, workflow logs, caches, or forks may retain it.
