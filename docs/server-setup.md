# Demo server setup record

Designed and developed by Vamsi Pinnaka

Recorded **October 5, 2026** for **https://volleyball.example.com**. This record describes the deployed demo VM; the [deployment guide](deployment.md) explains setup, release, and rollback procedures.

## Initial deployment

| Item | Recorded state |
| --- | --- |
| First application release | `bb3a658034a155f7a568074ceef307bfbce6a65f` |
| External HTTPS homepage | HTTP 200 |
| Plain HTTP | Redirects to HTTPS with HTTP 308 |
| Persistent Docker services | Exactly two: `app` and `postgres`; both healthy |
| Reverse proxy | Host Caddy; app listener bound to `127.0.0.1:5050` |
| PostgreSQL exposure | Private Docker network; no published database port |

## VM and component baseline

| Component | Actual configuration |
| --- | --- |
| Operating system | Ubuntu 26.04.1, amd64 |
| Memory | Approximately 1 GB RAM (956 MB reported) and 495 MB swap |
| Docker Engine | 29.8.2, official Docker CE packages |
| Docker Compose | 5.6.0 |
| Caddy | 2.11.7 |
| PostgreSQL container | `postgres:18.6-bookworm`; maximum 100 connections |
| App container | Gunicorn web service and export worker together |
| Gunicorn | 1 worker, 4 threads |
| App process identity | Container UID 10001; web service and export worker both alive |
| Restart behavior | Both containers use `unless-stopped`; host Docker and Caddy services enabled at boot |
| HTTPS certificate | Let's Encrypt certificate issued October 5, 2026; expires January 3, 2027 |

These conservative settings reflect the VM's small memory allocation. They do not establish production capacity, concurrency targets, availability, or recovery performance on this host.

## VM paths and persistence

| Path/resource | Purpose |
| --- | --- |
| `/opt/volleyball-manager` | Project deployment root |
| `/opt/volleyball-manager/releases/<commit-SHA>` | Retained committed source releases |
| `/opt/volleyball-manager/current` | Successful release pointer |
| `/opt/volleyball-manager/deploy` | Root-owned archive receiver, SSH command wrapper, and database initialization script |
| `/opt/volleyball-manager/config/runtime.env` | Nonowner app connection and application configuration |
| `/opt/volleyball-manager/config/operator.env` | One-shot migration and operator backup connections |
| `/opt/volleyball-manager/config/postgres.env` | Database initialization credentials |
| `/opt/volleyball-manager/config/release.env` | Selected application image |
| `/opt/volleyball-manager/data/instance` | Persistent private application files |
| `volleyball-manager-postgres-data` | Persistent PostgreSQL Docker volume |

Private configuration is root-owned mode 0600 under a root-only directory; these permissions were checked on the VM. Source updates preserve configuration, database storage, and private application files. Migration owner credentials are absent from the normal running app container.

## GitHub automatic deployment

The private repository is `OWNER/REPOSITORY`. The workflow deploys enabled pushes to `main` and supports manual dispatch on `main`. It streams an immutable source archive over verified SSH to the dedicated deployment account, builds the commit image on the VM, migrates once under a host lock, restarts the app, and requires readiness.

The following Actions secrets are configured; their values are intentionally absent from this record:

- `DEPLOY_HOST`
- `DEPLOY_USER`
- `DEPLOY_SSH_KEY`
- `DEPLOY_KNOWN_HOSTS`

`DEPLOY_PORT` is optional and currently uses the workflow's port 22 default. Repository variable **`DEPLOY_ENABLED=true`** has been set and verified. The first release was deployed through the operator connection. Initial run [#1](https://github.com/OWNER/REPOSITORY/actions/runs/37367922793) skipped deployment because the enable variable was still unset when it was evaluated; its setup-only notice job did not deploy code.

Manual run [#2](https://github.com/OWNER/REPOSITORY/actions/runs/37368139301) and push run [#3](https://github.com/OWNER/REPOSITORY/actions/runs/37368878393) failed while waiting for a GitHub-hosted runner. Run #3 reports “The job was not acquired by Runner of type hosted even after multiple attempts” and an internal server error. GitHub reported [an October 5 Actions incident delaying hosted-runner assignment](https://www.githubstatus.com/incidents/3q1yb5m7ltvb). Actual GitHub-triggered completion remains **pending verification**.

The dedicated deployment key was used directly with the workflow's exact verified SSH upload and forced receiver command. That release completed migrations, restarted the app, and passed container and loopback readiness. This verifies the VM deployment path; execution by a GitHub runner remains pending.

## Outstanding setup

- Verify the queued GitHub deployment completes and a real subsequent code update reaches the VM.
- The first platform administrator has been provisioned; complete MFA enrollment in Profile and security.
- Organization invitations use [manual account delivery](organizer-onboarding.md), selected by the owner, and do not require SMTP. The optional email password-reset adapter remains deferred.
- Select and configure a licensed weather provider; weather is disabled.
- Configure independently stored encrypted backups, continuous WAL/PITR, private-file/config recovery, retention, and backup alerts; no off-site recovery destination is configured.
- Complete and record the target-host restore, capacity, real-device, and accessibility gates before hosting production data.

The empty demo installation contains no copied development database or inherited local demo accounts. Persistent storage and healthy containers do not establish backup coverage or the proposed production recovery targets.
