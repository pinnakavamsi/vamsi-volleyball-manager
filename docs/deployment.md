# Manual self-host deployment

Designed and developed by Vamsi Pinnaka

The deployment files are an optional single-host template: host Caddy serves HTTPS, and Docker Compose runs an application container (Gunicorn plus export worker) and private PostgreSQL. Application files and the database persist outside release source/images. The repository's CI checks source/container builds; it does not upload code to a VM or carry a preconfigured deployment connection.

This guide does not describe an existing server. Use infrastructure you control, and keep host addresses, private configuration, keys, and operational records outside the public repository.

## Host preparation

Review `deploy/install-host.sh` before running it on a supported systemd Ubuntu/Debian host. It installs Docker Engine, Compose, and Caddy. Allow public HTTP/HTTPS only where required, keep PostgreSQL private, and control administrator access separately. Review available memory, disk, and capacity against your actual workload.

From a reviewed source checkout already present on your own host:

```sh
sudo bash deploy/install-host.sh
sudo bash deploy/configure.sh --domain YOUR_HOSTNAME
```

Replace `YOUR_HOSTNAME` with an HTTPS DNS hostname you control. Arrange DNS and reachable certificate challenge ports. The configurator renders the generic Caddy template, generates independent random signing/MFA/database credentials, and stores private root-owned mode 0600 configuration under `/opt/volleyball-manager/config`. It preserves complete existing configurations and refuses a changed hostname or partial configuration requiring review.

`runtime.env` contains only the application runtime configuration and nonowner database connection. `operator.env` contains migration/backup connections for short-lived commands. `postgres.env` holds database initialization configuration, and `release.env` selects the successfully released image. Do not copy development `.env`, databases, file storage, or accounts to the host. The persistent private application directory is `/opt/volleyball-manager/data/instance`; PostgreSQL has a separate Docker volume.

The installer also includes a constrained release receiver. No deployment key is required for the manual procedure below. Privileged host administration and source approval remain operator responsibilities.

## Apply a reviewed release

Use a committed, reviewed source revision. On the host, create an archive from that revision and pass it to the installed local receiver:

```sh
release_revision=$(git rev-parse HEAD)
release_archive=$(mktemp /tmp/volleyball-release-XXXXXX.tar.gz)
git archive --format=tar.gz --output "$release_archive" "$release_revision"
release_digest=$(sha256sum "$release_archive" | cut -d ' ' -f 1)
sudo /opt/volleyball-manager/deploy/receive-release.sh   "$release_revision" "$release_digest" < "$release_archive"
rm -- "$release_archive"
```

The receiver validates the checksum and bounded archive, rejects unsafe/private paths, stores an immutable source release, and invokes the release script under deployment locks. The script builds the selected image, starts private PostgreSQL, stops web/worker for maintenance, runs Alembic once with operator credentials, restarts the app, and checks container and HTTP readiness before recording success. This single-host procedure has a brief maintenance window. Pending browser actions still need normal server acknowledgment after connectivity returns.

Before releasing against existing data, verify a current independently stored recovery package and review migrations for compatibility with the retained application image. A failed migration/readiness check attempts to restore the previous application image while preserving database/files/secrets. It never automatically rewinds acknowledged games or downgrades schema. An incompatible migration may require reviewed forward repair or isolated recovery.

## First access

A fresh database has no preloaded accounts or competition data. Visit your own HTTPS application and complete first-platform-administrator signup. Choose your own email, name, and password; first-run setup closes after creation. Complete this step while access is controlled, then enroll MFA in Profile and security. Create organizations and separate organizer administrators through platform administration.

The explicit `bootstrap-admin` operator CLI remains an alternative. If used inside a short-lived container, include the operator overlay only for that command, never for normal service startup:

```sh
sudo docker compose --project-name volleyball-manager   --env-file /opt/volleyball-manager/config/release.env   -f /opt/volleyball-manager/current/compose.yaml   -f /opt/volleyball-manager/current/deploy/compose.operator.yaml   run --rm --no-deps app flask --app volleyball bootstrap-admin --email YOUR_ADMIN_EMAIL
```

Enter the password at its hidden prompt. No default account or password is published. Organization invitations use manual delivery; optional password-reset email, address lookup, and weather require separately configured services and suitable provider terms. External providers are disabled by default.

## Operations and rollback

Inspect container health and private application logs on your host:

```sh
sudo docker compose --project-name volleyball-manager   --env-file /opt/volleyball-manager/config/release.env   -f /opt/volleyball-manager/current/compose.yaml ps
sudo systemctl status caddy --no-pager
```

For a compatible retained application image:

```sh
sudo bash /opt/volleyball-manager/current/deploy/rollback.sh
```

Rollback changes application code only. Confirm schema compatibility first. Preserve the previous/current release images, PostgreSQL volume, and private files until the release is verified. Root-owned installer/receiver/SSH wrapper changes require an explicit reviewed host update. Database major upgrades and password rotation are separate operator work; changing initialization environment values does not change passwords in an existing volume.

For private runtime configuration changes, recreate the app service after review:

```sh
sudo docker compose --project-name volleyball-manager   --env-file /opt/volleyball-manager/config/release.env   -f /opt/volleyball-manager/current/compose.yaml   up -d --no-deps --force-recreate app
```

Use the [operations runbook](operations-runbook.md), [self-host verification checklist](server-setup.md), and [release checklist](release-checklist.md). Local/container readiness does not prove public DNS, HTTPS, production performance, independently durable storage, or disaster recovery. Configure encrypted recovery storage, continuous WAL/PITR, file/config recovery, retention, alerts, and an isolated restore drill before admitting live events. Keep recovery keys separately protected.
