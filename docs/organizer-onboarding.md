# Organizer accounts and organization access

The owner selected manual account delivery on October 5, 2026: the platform administrator creates an organization and its first staff administrator, shares a username and temporary password, and that administrator changes the password and creates additional staff accounts.

## Platform and organization dashboards

Platform administrators land on a dedicated dashboard with **Organizations**, **Backups**, **Performance**, and **Service health**. Tournament and game navigation is shown to organization staff. Provision the first organization administrator using a separate organizer email so that account opens the competition workspace.

Performance displays a current database query latency sample, connection pool use, connected database backends, database size, and cumulative transaction/deadlock counters. These values are actual request samples and PostgreSQL counters; they are not historical request rates or browser latency measurements. Transaction rollbacks can include normal read-only request cleanup.

Backups shows configured status, the last recorded successful package, and continuous archive status when available. The demo VM currently has no independent backup destination. The dashboard does not run privileged database backups or claim that a local status file proves an independently recoverable backup.

## Create an organization

1. Open **Platform administration → Provision organization**.
2. Enter the organization name, a unique URL slug, timezone, and first organization administrator's email and name. The email is the login username. Use the staff account's email rather than the platform administrator's email.
3. For a new organizer identity, the application displays a generated temporary password once. Share it privately with that organizer. The database stores a password hash; the password is not saved in reports, audit records, or invitation emails.
4. If the email already has an organizer account, its password stays unchanged. That organizer signs in with their existing credentials.

## First sign-in

1. Open **https://volleyball.example.com/login** and sign in with the supplied email and temporary password.
2. Replace the temporary password with a different password of at least 12 characters. Workspace access is blocked until this change succeeds.
3. Choose **Accept invitation** for the organization shown in the app. This records membership activation and opens its workspace.
4. Use the organization selector to switch between active memberships. Platform administration alone does not grant access to private organization data.

## Add staff

An organization administrator opens **Organizer staff**, adds an organizer's email, name, role and tournament scope, and shares the one-time credentials when a new account is created. The new organizer follows the same first-sign-in flow. Existing accounts receive an in-app invitation without a password replacement or any change to their other memberships.

## Organizations created before this change

An existing organizer can accept their pending invitation inside the app. If the first administrator has never received a password and has a passwordless invited identity, the platform administrator can issue initial credentials from the organization's row. This action does not replace an existing organizer's password.

If an organization was originally created with only a platform account as its administrator, choose **Set up organization administrator** to add its first separate organizer account. This provisioning action is available only while the organization has no active or invited independent administrator.

Invitations expire after 72 hours. Platform administrators can choose **Renew invitation** for the original pending administrator; their existing password stays unchanged. Organization administrators can reissue other staff invitations. Keep temporary passwords private. Email delivery is not used for organization invitations; the optional email password-reset feature needs a configured adapter.

## Verification

The complete isolated PostgreSQL suite passed **123 tests in 29.62 seconds** after this change. Coverage includes temporary password enforcement, first administrator/staff creation, existing password and membership preservation, CSRF, MFA, invitation expiry/renewal, acceptance races, prevention of account claims across organizations, actual performance metrics, and backup status validation. A migration downgrade/upgrade check verified that existing queued invitation payloads are converted to manual delivery while their membership references remain intact.

Local Chrome checks with synthetic accounts confirmed the platform landing page and Organizations, Backups, Performance, and Service health navigation; the temporary password screen; explicit invitation acceptance; and separate organizer workspace navigation. A synthetic staff account changed its password and activated its membership through the API before the browser workspace check. Production customer records were not used for these mutations.

Designed and developed by Vamsi Pinnaka
