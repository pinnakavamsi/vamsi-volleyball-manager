# Open-source release preparation

Prepared October 11, 2026. The application is licensed under MIT; bundled fonts retain their SIL Open Font License. The source requirements document remains a historical specification; the confirmed empty-installation and first-access setup policy takes precedence over its original automatic demonstration-delivery proposal.

## Installation behavior

Normal startup creates no application accounts, organizations, tournaments, or games. The first browser access asks the owner to choose a name, email, and password. A successful setup creates only the platform administrator and closes setup permanently. Concurrent setup requests cannot create multiple owners. Deactivating, demoting, or deleting an administrator does not reopen public setup. Explicit organizer provisioning and synthetic test fixtures remain separate workflows.

Configuration examples contain placeholders. Infrastructure credentials are generated locally or supplied by the operator. Ignored environment files, private storage, databases, backups, logs, access files, and SSH keys are excluded from Git, Python distributions, and the Docker build context. Optional weather and geocoding integrations start disabled.

## Verification

- The complete local PostgreSQL suite passed **146 tests** in 38.50 seconds. It includes eleven first-access setup/security/concurrency tests and five installation-script checks.
- The local application database was verified to have zero users, organizations, tournaments, matches, and setup-completion records after reset. Synthetic regression fixtures were cleared from the separate test database.
- Blank desktop and 390-pixel mobile setup screens were inspected. All account fields were empty, and the mobile page had no horizontal overflow. See [screenshots](screenshots.md).
- Source and wheel packages built successfully. The wheel includes the application assets, fonts, and required license notices. The source distribution includes migrations, scripts, tests, documentation, and screenshots; neither package includes private runtime files.
- Markdown links, Python and shell syntax, and CI YAML checks passed. The CI workflow runs the PostgreSQL tests and builds a container without deploying to a server.
- Hosted CI and Docker image execution were not run locally because a Docker daemon was unavailable. Physical-phone, provider, production recovery, and capacity checks remain recorded separately in the [release checklist](release-checklist.md).

## Repository and deployment privacy

The existing eight commits were reviewed, including their messages. No personal login email, private SSH key, access token, or saved infrastructure credential was found in reachable committed content. Old operator references and the former deployment workflow are removed from the prepared release history. The previous audio asset lacked a provenance record; its historical copies are excluded, and the current whistle is reproducible mathematical synthesis with MIT provenance.

The old automatic VM deployment workflow is removed. GitHub deployment credentials, its enable flag, the former VM environment, and the seven approved historical deployment runs/logs were removed. Local deployment private-key files and matching host-trust records were removed. This preparation does not reset a deployed application's database. Server-side SSH authorization is an operator console action when no administrative SSH credential is available.

Publishing rewritten history changes commit identifiers. Existing collaborators should make a fresh clone and avoid merging old history into the public branch. A force push removes old references but cannot erase copies in other clones or guarantee removal of GitHub's cached/dangling views; see [GitHub's sensitive-data guidance](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository). No old credential is relied on for the new distribution.
