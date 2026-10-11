# Contributing

Discuss substantial behavior or schema changes in an issue before implementation. Include the problem, proposed behavior, and relevant requirement or acceptance scenario. Small fixes can go directly to a pull request.

## Development

Follow the [local setup](README.md#local-development). Use PostgreSQL for application and integration tests; SQLite does not exercise the row locks, roles, and tenant policies used by this project. Keep local secrets and generated files under ignored paths. Never use production or customer records as fixtures.

Run the [isolated PostgreSQL checks](README.md#validation) relevant to your change. Explain the checks performed and any checks you could not run in the pull request. For interface changes, verify keyboard behavior and narrow mobile layouts. For authorization, scoring, court assignment, or migration changes, include a regression that exercises the relevant permission or concurrency boundary.

## Implementation conventions

- Keep scoring and ranking rules in backend services. Accepted scoring actions and audit history are durable PostgreSQL records.
- Enforce organization, tournament, and delegated-referee permissions on the server. Platform administration does not imply access to private customer events.
- Preserve accepted actions and independent memberships. Use reviewed migrations and audited corrections for consequential changes.
- Clearly distinguish unavailable services, queued actions, provisional results, and completed operations.
- Use synthetic, explicitly disposable fixtures. Normal startup and first access must remain empty until the installer creates an account and records.
- Keep credentials, QR grants, personal contacts, database dumps, logs, and host-specific configuration out of patches, issues, screenshots, and documentation.

Review [AGENTS.md](AGENTS.md), the [requirements](Requirements/volleyball_v1_codex_requirements.txt), and the [release checklist](docs/release-checklist.md) for scope and open acceptance gates. Follow the [code of conduct](CODE_OF_CONDUCT.md). Report vulnerabilities through [SECURITY.md](SECURITY.md), rather than a public issue containing exploit details or private records.

Contributions are accepted under the repository's MIT license. Preserve third-party license notices, including the bundled fonts' SIL Open Font License.
