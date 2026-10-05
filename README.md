# Volleyball Manager

A mobile browser application for organization tournament/league administration, private delegated refereeing, and public spectator/display experiences. All project files live in `VOLLEYBALL-MANAGER`; neighboring workspace projects are independent.

Designed and developed by Vamsi Pinnaka

The local build uses Python (tested with 3.14), Flask, SQLAlchemy 2, Alembic, PostgreSQL 18 and modular HTML/CSS/JavaScript. Dependencies are pinned in `requirements.txt`. PostgreSQL holds accepted scoring actions, projections, court ownership, sessions, results, rules and audit history. Browser storage holds only preferences and clearly Pending unsent actions. The demo VM deployment package is described below; external mail/weather/recovery providers and production acceptance remain separate configuration and release work.

## Start the local application

Install PostgreSQL 18 client/server tools and Python 3.12+ (3.14 tested). Keep this database separate from production. With `uv` available:

```sh
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python scripts/start_local.py
```

The launcher prepares the isolated database, applies migrations, preserves existing demo data, and starts the local Gunicorn web service plus export worker. Press Ctrl+C to stop web and worker; PostgreSQL remains available. Restart with `.venv/bin/python scripts/start_local.py`. The web service uses two workers with eight threads each, binds only `127.0.0.1:5050`, and writes private project logs under `instance/`. API logs record bounded request timing and scoped IDs without request bodies or credentials.

The local database helper creates only this project's cluster under `instance/postgres`, starts loopback PostgreSQL on port 55432, creates the nonowner runtime/test roles/databases and a separate operator backup role, and writes random private credentials into `.env` (0600). It preserves existing credentials. Its default PostgreSQL binary directory is `/opt/homebrew/opt/postgresql@18/bin`; set `POSTGRES_BIN` for another installation and `POSTGRES_PORT` for an available port. Startup may require permission to bind localhost on a managed machine.

Open `http://127.0.0.1:5050`. Random local organizer and platform login credentials are in `instance/demo-access.txt`; that private file is excluded from version control. No hardcoded account password is used. The explicitly labeled demo contains two organizations sharing one organizer, a multi-division tournament with live/ended/submitted results and ranking ties, and a recurring league across months. The organizer switches organization scope in the application. Platform administration is a separate identity/binding and does not grant private customer access.

To start individual components for development instead of using the launcher:

```sh
.venv/bin/python scripts/local_database.py
.venv/bin/alembic upgrade head
.venv/bin/python scripts/prepare_demo.py
.venv/bin/flask --app volleyball run --host 127.0.0.1 --port 5050
```

In that case, use a second terminal for export processing:

```sh
.venv/bin/flask --app volleyball worker
```

Use `worker --once` for a single batch. Jobs remain persisted when the worker is stopped; the UI reports queued/running/completed/failed/cancelled/expired state accurately. Configure private durable file storage before adding production replicas. Local active scoring truth is shared PostgreSQL data.

## Setup a supplied PostgreSQL service

Use `.env.example` for the environment variable contract. `DATABASE_URL` must use a nonowner runtime role without superuser/BYPASSRLS; `MIGRATION_DATABASE_URL` is the controlled migration owner. `TEST_DATABASE_URL` and `TEST_RUNTIME_URL` must identify a separate isolated test database. Keep credentials out of source control. Apply `alembic upgrade head` once per controlled release, then start replicas/workers. The migrations establish tenant policies, relational consistency, court/event uniqueness and runtime grants; examine their role assumptions before using a differently named runtime role.

The generated `BACKUP_DATABASE_URL` uses the distinct `volleyball_backup` role: NOSUPER with REPLICATION/BYPASSRLS and required read rights for complete operator recovery packages. It is never the application runtime connection. The local initdb owner is a bootstrap superuser inside this isolated development cluster; that convenience is separate from production's reviewed provisioner/migration/runtime/backup role design. Production readiness rejects an application connection that is a superuser, bypasses RLS or inherits table ownership.

Bootstrap a first platform administrator securely, using the prompted password:

```sh
.venv/bin/flask --app volleyball bootstrap-admin --email YOUR_ADMIN_EMAIL
```

Enroll MFA in Profile and security. Administrators can require MFA for their organization. Production configuration requires HTTPS, secure cookies, a strong application secret and a separate `MFA_ENCRYPTION_KEY`.

Organization and staff onboarding uses [manual temporary credentials](docs/organizer-onboarding.md), as selected by the owner. Administrators share the credentials privately; new organizers must change their password before accepting organization access. Existing organizers keep their password and accept invitations inside the app. Email is not needed for this flow. The optional email password-reset adapter still requires configured mail delivery; `EMAIL_DELIVERY_MODE=deferred` leaves those messages pending. A local file outbox is development/test only. Do not use unsecured demo data or local random accounts as production provisioning.

## Verify implementation

The integration suite requires the actual isolated PostgreSQL database and nonowner runtime role. Migrate the test database without displaying secrets:

```sh
.venv/bin/python - <<'PY'
import os, subprocess
from dotenv import load_dotenv
load_dotenv('.env')
os.environ['MIGRATION_DATABASE_URL'] = os.environ['TEST_DATABASE_URL']
subprocess.run(['.venv/bin/alembic', 'upgrade', 'head'], check=True)
PY
.venv/bin/python -m pytest -q
```

Tests truncate only the guarded isolated test database; run them serially. The latest local PostgreSQL suite passed **123 tests in 29.62 seconds**, covering manual account onboarding, platform metrics, identity/isolation, rules/formats/rankings, court/scorer races, accepted action recovery/deduplication, reports/imports/export revocation, PDF fonts and safe sponsor configuration. A local encrypted physical/WAL restore drill passed, including a score acknowledged after the base backup; its [record](docs/restore-validation.json) reports 1.795 seconds isolated restoration and 17.502 seconds for the complete local drill. This small local measurement does not prove production RPO/RTO. Development source archiving was returned to disabled after the drill; configure independent storage/archiving before production. The release checklist distinguishes automated checks from required real iPhone/Android/accessibility, licensed provider, target-host performance and production recovery work. PostgreSQL owner credentials are not used to prove runtime RLS.

The [recorded local HTTP baseline](docs/load-validation.json) used 25 scoring games/500 distinct viewers across two organizations, two Gunicorn workers/eight threads each, and one-second compact polling. p95 durable score acknowledgment was **842.819 ms** and p95 Saved-to-public visibility was **1,718.661 ms**, meeting the proposed local p95 thresholds. It accepted 479 Saved actions and 6,606 live reads; bounded backpressure skipped 21 writer ticks and 6,742 viewer ticks. All 119,750 expected committed-version observations arrived, with zero request errors/lost Saved actions/committed duplicates/scope leaks. This records the tested workload with backpressure; it does not prove sustained 500 successful reads/second or production capacity. Some delivery tails exceeded two seconds. Loopback excludes TLS/proxies/mobile networks; field LCP/INP and real devices remain unmeasured.

The [PDF artifact review](docs/report-pdf-review.md) inspected all four pages of a synthetic wide standings export and fixed row-identity and extended-Latin font defects. Device/accessibility and production-provider/recovery gates remain in the [release checklist](docs/release-checklist.md).

The [local Chrome workflow review](docs/browser-review.md) includes setup, QR countdown/admission, Saved scoring/takeover, a 320-pixel referee view, reports/dashboard refresh, privacy clearing and application restart recovery. It uses desktop Chrome and narrow viewport emulation; physical phone/TV and assistive-technology checks remain separate.

Inspect journal/projection consistency for an explicit organization:

```sh
.venv/bin/flask --app volleyball verify-projections --organization-id ORGANIZATION_UUID
```

If a reviewed projection rebuild is required, `rebuild-match --organization-id ... --match-id ... --confirm` reconstructs from immutable accepted journal snapshots and records an audit event. Do not treat it as a way to rewrite results. `publish-outbox` records bounded committed delivery readiness; clients use authorized polling and refresh current persisted snapshots after reconnection.

## Deploy the demo VM

The [deployment guide](docs/deployment.md) prepares `https://volleyball.example.com` with host Caddy and exactly two long-running Docker services: the application (Gunicorn plus export worker) and private PostgreSQL. Database data and private files persist independently of release source/images. Runtime database credentials are separate from one-shot migration/operator credentials.

Once the VM and GitHub Actions SSH secrets are configured and repository variable `DEPLOY_ENABLED=true` is set, each push to `main` uploads that immutable commit, builds its Docker image on the VM, migrates once under a deployment lock, restarts the app, and requires readiness before success. The private repository stays private; the VM needs no GitHub token. See the guide for first administrator setup, verified SSH host keys, rollback, and backup/recovery gates.

## Application and integration documentation

- [Versioned API and scoring/recovery examples](docs/api.md)
- [PostgreSQL relationships, indexes and RLS](docs/schema.md)
- [Reports, reviewed CSV imports, exports and portability](docs/reporting.md)
- [Weather adapter, licensing, cache and actual event-date horizon](docs/weather-adapter.md)
- [Operations, encrypted backups, continuous WAL/PITR, restore, migrations and incidents](docs/operations-runbook.md)
- [Caddy, Docker, private PostgreSQL and automatic GitHub deployment](docs/deployment.md)
- [Deployed demo server setup record](docs/server-setup.md)
- [Requirement traceability and real-device/release checklist](docs/release-checklist.md)
- [Local browser workflow evidence](docs/browser-review.md)
- [Report PDF visual evidence](docs/report-pdf-review.md)
- [Owner's V1 specification](Requirements/volleyball_v1_codex_requirements.txt)

Weather/geocoding are unavailable until a real licensed adapter is configured; no fabricated forecast is displayed. Team/player accounts, payments, custom builders, scheduled report distribution, native clients and additional competition formats are deferred by the specification. Five-minute disaster data-loss and sixty-minute recovery objectives and mobile/live performance thresholds are proposed production targets; local validation does not establish a production service guarantee.
