# Self-host verification checklist

Use this checklist for your own installation after following [deployment guidance](deployment.md). Keep hostnames, addresses, repository configuration, access keys, private environment files, and detailed operational records outside the public repository. This file does not describe an existing server or certify a deployment.

- [ ] Record the reviewed source revision, dependency versions, operating system, resources, PostgreSQL version, and worker configuration in private operator records.
- [ ] Verify DNS, HTTPS certificate issuance, HTTP-to-HTTPS behavior, and external access from a separate client.
- [ ] Confirm application listeners are bound as intended and PostgreSQL is reachable only by authorized private clients.
- [ ] Verify application readiness, the migration head, runtime role restrictions, RLS, and durable export worker operation.
- [ ] Confirm private database/file storage persists across application restarts and secrets remain restricted to their intended processes.
- [ ] On a new installation, complete first-administrator signup with your own credentials and enroll MFA. No organization or tournament should exist before explicit creation.
- [ ] Check manual organization/staff onboarding using disposable identities. Verify platform administration alone does not reveal private organization events.
- [ ] Configure email resets and licensed location/weather providers only if needed; exercise configured services without exposing credentials.
- [ ] Establish independent encrypted recovery storage, retention, monitoring, and an isolated restore drill.
- [ ] Complete physical-device, accessibility, target-host load, and recovery gates in the [release checklist](release-checklist.md).

Healthy containers and local readiness do not prove external HTTPS, production capacity, or recoverability. Record observed results and unresolved gates without publishing operator credentials or application data.

Designed and developed by Vamsi Pinnaka
