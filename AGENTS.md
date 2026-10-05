# Volleyball Manager project guidance

`VOLLEYBALL-MANAGER/` is the root for the Volleyball Manager project. Keep all project work, source files, documentation, generated artifacts, and project-specific notes inside this directory. Do not mix in files or context from other projects in the parent workspace.

The product requirements and acceptance criteria are in `Requirements/volleyball_v1_codex_requirements.txt`. Read and follow that file when planning or implementing features. Treat its MUST requirements and acceptance scenarios as release requirements, and keep proposed defaults distinct from confirmed product decisions as the requirements specify.

## Confirmed account and administration decisions

- The owner selected manual account delivery on October 5, 2026. Create organizations and their first organizer administrator accounts, display temporary credentials for private sharing, require a different password at first sign-in, and allow organization administrators to add staff. Organization invitations do not depend on email.
- Platform administrators use a dedicated dashboard for organization provisioning, backup status, performance, and service health. Their navigation and default landing page must show platform operations. Organization staff use the tournament and game workspace.
- Preserve existing organizer passwords and independent memberships. Platform administration does not implicitly grant access to customer tournament data. Show operational status from actual measurements and configuration.
