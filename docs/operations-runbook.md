# Operations and recovery

Project root: `VOLLEYBALL-MANAGER`. All commands run from this directory. The local adapter is a development implementation. Production hosting, durable storage, mail, licensed weather/geocoding and managed recovery architecture require operator selection and configuration before launch.

For the isolated local application, run `.venv/bin/python scripts/start_local.py`. It prepares this project's PostgreSQL cluster, applies the checked-out migration head, creates no application demo records, and starts loopback Gunicorn plus the durable export worker. Ctrl+C stops web/worker cleanly and leaves PostgreSQL available; repeat the command to restart. Local Gunicorn uses two workers/eight threads each. Private `instance/application.log` and `instance/worker.log` hold diagnostics; request logging records duration/request and scoped identifiers without credential/request payloads. This local launcher is not a production process manager.

## Roles and release ownership

Use distinct database roles: a migration owner, a nonowner application runtime with no superuser or BYPASSRLS privilege, and a restricted operator backup role. Give the runtime only application table/sequence privileges; migrations run once through a controlled release step. Never give browsers database credentials. Transaction-local organization context is cleared by commit/rollback before connection reuse. Verify RLS with the runtime role, including two organization requests using pooled connections. A migration-owner or superuser connection is unsuitable for proving RLS isolation.

Local setup generates a separate `volleyball_backup` role and private `BACKUP_DATABASE_URL`: NOSUPER with REPLICATION, BYPASSRLS and necessary SELECT rights so a full platform backup contains all organizations. Restrict that connection to recovery operators; the HTTP application always uses its nonowner NOSUPER/NOBYPASSRLS role. Local initdb's owner is a bootstrap superuser for an isolated development cluster. In production, separate provisioner privilege from ordinary migration ownership and review the replication/backup grants with the selected provider. A complete backup role legitimately bypasses tenant policies; a runtime role must not.

Before production launch, assign an incident owner, database/recovery operator, and organization communications contact. Store their escalation contacts in operator configuration. The platform administrator is an operations identity; private organization access still requires a valid scoped membership or an explicitly approved and audited support process.

## Health, workers and monitoring

`GET /api/v1/health/live` proves the application process responds. `GET /api/v1/health/ready` checks PostgreSQL connectivity, the expected migration head, a nonowner NOSUPER/NOBYPASSRLS runtime (including inherited ownership), and enabled tenant RLS; readiness failure removes a replica from traffic. Set `SCHEMA_REVISION` for a pinned release, or the local checkout's Alembic head is used. Neither response discloses credentials. Authenticated platform administrators use `/api/v1/operations` for queue/outbox backlog, scorer heartbeat counts and configured backup status. This endpoint is not public.

Run the export worker separately from HTTP replicas: `flask --app volleyball worker --once` for one batch; omit `--once` for the continuously polling worker. PostgreSQL stores queue state. The worker claims jobs with row locking and rechecks current organization status, requester membership and tournament permissions. Downloads recheck them again. Configure durable private `PRIVATE_STORAGE_PATH` and start the worker after migrations. On worker failure, pending jobs stay queued; interrupted running jobs are reclaimed after their lease expires. Expired files are deleted by the worker cleanup pass. A request's source metadata identifies source result/match generations; a later report can legitimately differ after correction.

Monitor uptime, readiness failures, API latency/error rates, PostgreSQL pool utilization and slow queries, outbox/worker backlog age, active scorer heartbeat age, storage capacity, and backup age/failure. The included endpoint supplies snapshots rather than a hosted metrics/alerting service. Configure centralized redacted logs and alerts with the chosen provider; never log raw QR/session/reset secrets or private report payloads. Define log, audit, import and export retention policies before launch. A disconnected referee does not release its court or finish its game.

## Encrypted logical backup adapter

Install PostgreSQL client tools matching the server and `age`. Set a dedicated operator `BACKUP_DATABASE_URL` and public `BACKUP_AGE_RECIPIENT`; keep the private age identity in a separately protected recovery secret store. Prefer a PostgreSQL service/passfile over an interactive command containing a password. Quiesce durable file mutations or snapshot storage while collecting the database and file bundle. Back up deployment/configuration recovery material using `--recovery-config` only when approved; the whole bundle is encrypted.

```sh
python scripts/backup.py --files ./instance/files --storage ./operator-backups
```

The output is an encrypted custom-format database dump plus complete durable file tree and SHA-256 manifest. The storage directory must be outside the application files directory. For local development, separate directories help prevent recursion but do not create a separate failure boundary. Production copies must go to independent, encrypted storage with restricted operator access; secure transport and retention lifecycle belong to that adapter/provider. Never store backup packages under public static assets or expose them to organization users.

The proposed retention is 30 days. The logical helper is a useful restore package; it does not implement WAL archiving, incremental backups, automatic retention, offsite upload or a five-minute recovery point. The physical/WAL adapter below supplies separate local PITR mechanics. Verify backup alerts and old-copy deletion in the selected platform. Do not claim that the proposed five-minute data loss and sixty-minute recovery targets are measured until a complete timed drill proves them.

## Local continuous WAL and physical recovery adapter

The native adapter uses streaming AES-256-GCM authenticated encryption with a distinct random nonce per recovery object. Keys are 32 random bytes encoded as URL-safe base64 in a file readable only by its operator (0600). Keep an independent protected key copy; losing it makes recovery impossible. Scripts never return or log key material. These commands configure an operator-controlled isolated local PostgreSQL cluster; they do not alter a live production service.

Set `archive_mode=on`, `wal_level=replica`, `archive_timeout='60s'` and an `archive_command` invoking `scripts/backup_wal.py "%p" "%f" --storage ABSOLUTE_RECOVERY_DIRECTORY --key-file ABSOLUTE_KEY_FILE` with the application's Python environment. Use absolute script/interpreter/storage paths, shell quoting and PostgreSQL literal escaping. Restart the isolated source when enabling archive_mode. Configure localhost replication authentication and a dedicated replication/backup role for pg_basebackup. Check `pg_stat_archiver` successes/failures and `wal-status.json`; force an initial `pg_switch_wal()` and verify the encrypted archive. A successful archive exit follows encrypted write, authenticated file integrity and fsync. Repeated identical WAL files are safe; a same filename with different bytes is rejected. Use a separate storage tree per PostgreSQL cluster.

```sh
python scripts/backup_base.py --storage ./operator-backups/physical \
  --files ./instance/files --key-file ./operator-secrets/recovery.key
```

Load `BACKUP_DATABASE_URL` privately into the operator process environment; an explicit `BASEBACKUP_DATABASE_URL` can override it with another replication/operator connection. Do not use the HTTP runtime role. The command verifies archive_mode and refuses external tablespaces unsupported by this adapter, streams a physical base and initial WAL with pg_basebackup, bundles durable files/checksums, and encrypts the package. Quiesce durable-file mutations or take a corresponding storage snapshot. Schedule a base backup at the selected frequency; schedule/version durable storage independently throughout the WAL window so files referenced by later recovered transactions are available. The local package contains files at base time; it cannot invent files created later. Production object storage should retain object versions aligned with the recovery window. A base plus every contiguous subsequent WAL segment is necessary for PITR. An archive_timeout reduces quiet-period archive lag but does not establish a measured five-minute RPO by itself.

The physical manifest captures source `max_connections`, `max_worker_processes`, `max_wal_senders`, `max_prepared_transactions` and `max_locks_per_transaction`. PostgreSQL recovery requires these values at least as large as the source; the restore adapter validates bounds and preserves/increases them. Older packages use explicit fallback values 100/8/10/0/64. A first local drill exposed an insufficient max_connections setting; that defect was corrected and covered by a focused recovery-parameter test. A successful rerun must be recorded separately from that failed attempt.

The corrected encrypted physical/WAL drill passed on October 5, 2026; see `restore-validation.json`. It replayed an acknowledged post-base score and verified two organizations/memberships, court ownership, rule/result/audit state, file integrity and runtime RLS. Isolated restoration took 1.795 seconds and the full local drill 17.502 seconds. Validation waited for PostgreSQL to finish recovery (`pg_is_in_recovery()=false`) before comparing restored state. These measurements apply to the small isolated local macOS/PostgreSQL 18 environment. Source development archiving was disabled after the drill. They do not establish production RPO/RTO, independently durable storage or an automatic quarterly recovery service.

```sh
RESTORE_ISOLATED=yes python scripts/restore_pitr.py BASE_PACKAGE.tar.enc \
  --storage ./operator-backups/physical --key-file ./operator-secrets/recovery.key \
  --destination ./instance/restore-validation --port 55434 --start
```

Use compatible PostgreSQL client/server versions. The destination must be empty. The script decrypts/authenticates the complete base, checks file hashes, rejects archive traversal/symlinks/devices, replaces primary-specific PostgreSQL settings, creates recovery.signal and restores requested encrypted WAL using `restore_wal.py`. It starts only an explicitly requested isolated server, bound to loopback with its own port. Unix sockets are disabled by default because long workspace paths exceed system socket limits. TCP authentication uses the restored source-role password with scram. An optional `--socket-dir` enables a private 0700 socket directory with an absolute path under 90 bytes; trust then applies only to that protected socket. An optional aware `--target-time` configures time recovery and pauses at the target for operator review; resume/promote only after validation. With no target, PostgreSQL replays all available contiguous WAL and completes recovery. Missing future WAL is expected when archive replay reaches the end; a gap inside required WAL is a failure requiring investigation. Read `recovery.log`, verify `pg_is_in_recovery()`/replay position and accepted application events before claiming success. Stop the isolated server after a drill.

Native encryption and continuous archiving are local functional adapters. Independent storage, secure transport, scheduled retention, file versioning, alert delivery and production recovery objectives still require hosting/storage configuration and a measured operator drill. Never claim that local directories alone provide an independent failure boundary.

## Restore procedure

1. Declare the incident and restrict writes if consistency is threatened. Preserve forensic material and current database/storage snapshots. Ordinary app/browser crashes must retain acknowledged scoring events; restore is for infrastructure/data incidents.
2. Create a private isolated PostgreSQL database and empty isolated file directory using the recovery operator. Keep outbound email, public access and external callbacks disabled.
3. Set `RESTORE_DATABASE_URL` to the isolated database and `RESTORE_ISOLATED=yes`. Ensure `DATABASE_URL` identifies the primary database so the helper can reject an identical destination. The target public schema must be empty. Keep recovery identity outside the application repo.

```sh
python scripts/restore.py ./operator-backups/volleyball-TIMESTAMP.tar.age \
  --identity /secure/location/recovery.agekey \
  --files ./isolated-restore/private --report ./isolated-restore/restore-result.json
```

4. The helper decrypts, rejects unsafe archive members, verifies database and file hashes, restores in a single transaction, and copies files only to the isolated directory. Reapply the intended database-role grants and RLS context setup; the restore intentionally omits source ownership/ACLs. Verify the migration version and application configuration match the recovered database.
5. Validate accepted event sequence/version continuity, unique client-action receipts, rule snapshots, current projections, results/revisions, audit references, membership isolation, active courts/scorer generations and stored file metadata. Compare representative match state with event journal replay. Verify revoked access remains revoked and public/private visibility behavior. Checksums validate bytes; they do not prove application relationships or authorization.
6. Record source timestamp, disaster window, measured restoration duration, rows/files validated, discrepancies and reviewer approval in a restore drill record. Decide recovery promotion and communicate the expected data window to organizers through the configured incident contacts.
7. Test a complete restore before production launch and at least quarterly. The provided helper and checklist are executable preparation; a scheduled, measured recovery service remains an operator obligation.

## Incident-specific recovery

Database failure: fail traffic readiness, repair the service or perform isolated recovery/PITR, validate, then restore traffic. App outage: restart/roll back compatible code without rewinding persisted games. File loss: restore missing durable objects and verify DB references; never invent missing evidence. Failed migration: stop the release, preserve a snapshot, inspect Alembic state, prefer a tested forward fix; an incompatible reversal requires the isolated restore procedure. Incorrect data: use audited result corrections or selective reconciliation rather than rewinding the entire platform.

To recover one organization, restore the complete platform into isolation, select that organization's supported records, review foreign keys and current revisions, and reconcile under an audited migration plan. Preserve other organizations and shared global users/memberships. The business JSON export is portable ownership data, not a disaster-recovery package, and there is deliberately no organization-level platform rollback endpoint. Selective reconciliation is a reviewed operator procedure; the application does not offer unattended merge/import of a historical organization backup.

## Deployments, replicas and secrets

Use separate development, isolated staging/test and production databases and storage. Run `alembic upgrade head` exactly once before starting new replicas, with a release lock and a reviewed backup. Use compatible expand/contract migrations for rolling deployments and retain a known-good code artifact. Health check after migration; seed only explicitly isolated demo data. Multiple replicas share PostgreSQL and private durable storage. No active match truth may depend on a replica's memory. Authorized polling consumes committed projections; queued/outbox records remain durable through process restart.

Public viewers fetch one full overview, then poll `/api/v1/public/<slug>/live` for bounded current projections with bulk participant/court lookups. Visibility is authorized on every poll. Configuration/standings generation or match-ID changes trigger a full refresh; ordinary point updates do not reread rankings or event history. Measure this actual browser pattern through the deployed HTTP workers, including request scheduling/network delay. A one-process in-process generator measurement and fast isolated SQL plans do not establish the production concurrency/latency targets.

Production requires HTTPS, secure cookie configuration, allowed-origin policy, managed secrets, mail delivery for password resets, and persistent file storage. Rotate signing/application secrets through a documented maintenance window; rotation can invalidate sessions. Revoke admission/scorer grants through organization game controls, rotate database credentials with overlapping roles then remove old credentials, and rotate external provider/storage/backup credentials through the operator secret store. Never commit secrets into `.env.example` or demo records.

## Organization offboarding and retention

An organization administrator can export supported business records via `/api/v1/organizations/export`. Audit that export, archive competitions and disable the organization/memberships under the platform policy. Do not delete shared global identities or historical audit actors merely because one membership is removed. Define legal/product retention and a reviewed deletion procedure before production use. Backup retention can delay physical deletion; encrypted backup lifecycle and erasure rules must be documented for the chosen storage. Existing copies of formerly public information cannot be recalled.
