# Security

## Reporting a vulnerability

Use the repository host's private vulnerability reporting feature when it is available. Include affected versions, steps to reproduce using synthetic records, the security boundary affected, and a proposed mitigation if known. Do not include working credentials, QR/session/reset secrets, customer records, or database backups.

If no private reporting channel is configured, open an issue requesting a private security contact without publishing vulnerability details. Maintainers will arrange a private channel. No response-time or support guarantee is established by this document.

## Deployment responsibilities

This is self-hosted software. Operators manage patching, HTTPS, secret storage, PostgreSQL role separation, licensed external services, recovery storage, and incident response. Read the [operations runbook](docs/operations-runbook.md) and [release checklist](docs/release-checklist.md) before hosting live events.

An empty installation exposes first-administrator signup. Keep first access under the installer's control until setup completes. Setup requires the application's origin and CSRF protections, is throttled, and closes after the first administrator is created. It does not create organizations, staff, or tournament data. Require MFA for privileged accounts and preserve its encryption/recovery keys securely.

Keep `.env`, `instance/`, operator configuration, private files, backups, logs, database data, and deployment keys outside published source and package artifacts. Revoke or rotate exposed credentials before changing history; removing a file does not invalidate a leaked credential or remove historical copies.

Release and application security checks are described in the repository. Physical-device, accessibility, production capacity, independent recovery, and external-provider acceptance still require operator validation. The project does not currently publish a maintained-version support matrix; use a reviewed release and track repository advisories.
