# Volleyball Manager project guidance

`VOLLEYBALL-MANAGER/` is the root for the Volleyball Manager project. Keep all project work, source files, documentation, generated artifacts, and project-specific notes inside this directory. Do not mix in files or context from other projects in the parent workspace.

The product requirements and acceptance criteria are in `Requirements/volleyball_v1_codex_requirements.txt`. Read and follow that file when planning or implementing features. Treat its MUST requirements and acceptance scenarios as release requirements, and keep proposed defaults distinct from confirmed product decisions as the requirements specify.

## Confirmed account and administration decisions

- The owner selected manual account delivery on October 5, 2026. Create organizations and their first organizer administrator accounts, display temporary credentials for private sharing, require a different password at first sign-in, and allow organization administrators to add staff. Organization invitations do not depend on email.
- Platform administrators use a dedicated dashboard for organization provisioning, backup status, performance, and service health. Their navigation and default landing page must show platform operations. Organization staff use the tournament and game workspace.
- Preserve existing organizer passwords and independent memberships. Platform administration does not implicitly grant access to customer tournament data. Show operational status from actual measurements and configuration.
- Venues and courts are managed within each tournament and can be added after the initial tournament draft is saved. Organization venues form a reusable address book; selecting a saved venue must explicitly attach it to the tournament.
- Organizers enter or select an address instead of typing GPS coordinates. Address suggestions resolve map coordinates on the server, and venue weather uses the selected address. Provider usage and licensing must match the owner's confirmed commercial or community use.
- Assigning a court keeps the game window open so the organizer can reveal its referee QR without reopening the game. QR remains hidden until explicitly requested.
- Referee mobile scoring has a prominent whistle control with no press notification and automatic screen wake lock while scoring. Do not show a screen-awake button or imply browsers can prevent manual locking or override operating-system power policies.
