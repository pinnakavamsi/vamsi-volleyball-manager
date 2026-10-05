# V1 release evidence and outstanding validation

This is a local implementation and verification record. The source specification's MUST requirements are release gates. A functional local implementation does not prove production capacity, external service licensing, device behavior or a recovery service agreement. Check boxes below are operator/reviewer actions, not claims that an unperformed review passed. Test results are recorded separately from implemented features.

The implemented local modules include organizer authentication/MFA and independent organization roles; team/venue/court administration; draft competition setup, supported formats and rule history; durable game scoring/QR delegation, court ownership, takeover/recovery and result revisions; authoritative rankings and guarded progression; organizer/public/fullscreen interfaces; scoped reports, reviewed imports, durable export jobs and ownership export; unavailable/real-configured weather states; health/operations and encrypted logical/physical/WAL recovery adapters. Their source and setup are delivered in this project.

Remaining launch validation is concrete: real iPhone Safari/Android Chrome and assistive-technology checks; target-host mobile/load measurements; licensed weather/geocoding and real SMTP delivery configuration; independent durable/versioned storage, scheduled retention/WAL monitoring and quarterly drills; incident ownership and confirmed ranking/approval/QR/recovery/support policies. Geocoding is manual verified-coordinate entry in this build, not a connected external service. Source development WAL archiving was disabled after the isolated recovery drill.

## Implementation and acceptance traceability

| Acceptance | Implementation/evidence | Release validation |
| --- | --- | --- |
| A01–A04 | Global identities, independent organization memberships/roles/tournament scope; request and RLS checks; identity/isolation tests | Run real PostgreSQL suite with nonowner runtime role; verify role/tournament denials and pool context reuse |
| A05–A08 | Guided competition setup, rule validation/overrides/snapshots, formats service; isolated two-organization multi-division/league demo | Verify setup/ranking policy review, round robin repeats, pool qualifiers, seeded byes and cross-pool comparison policy |
| A09–A11 | Planned fixture times, no-court display, atomic court assignments/partial unique indexes, overlap/rest/venue checks | PostgreSQL races and conflict tests; inspect actionable schedule errors |
| A12–A16 | Explicit reveal API, ten-second elapsed deadline, background hiding, hashed game grant/exchange/session; public-safe serializer | Browser QR visibility/repeated reveals/background and secret inspection; confirm admitted scorer survives QR hide |
| A17–A21 | Journal/projections, snapshots, serve/timeouts/undo/set completion, accepted ID deduplication and optimistic versions | Score boundary/deciding/cap/fixed rules plus PostgreSQL stale/race tests |
| A22–A27 | Durable accepted events, generation takeover, device local Pending queue, state recovery and court persistence | Process restart; lost-response duplicate; stale old device/reconnection and cross-device unsent-state explanation |
| A28–A30 | Foreground wake lock lifecycle, accurate unavailable UI, bundled whistle/unlock/test | Real iPhone Safari/Android Chrome device matrix below; not proved by Python mocks |
| A31–A35 | Explicit End Game/release, submission/approval/revision history, authoritative standings/metrics/ties and guarded qualifier propagation | Approve/correct preview; downstream started match protection; full ranking edge-case review |
| A36–A40 | Anonymous public detail/live polling/filtering, privacy recheck, dedicated clean display, private/public layout separation, persisted dashboard data | Public/private switch while a viewer is active; fullscreen restrictions/credit; configured charts/table alternatives |
| A41–A42 | Disabled-by-default weather adapter, verified venue coordinates/local timezone/event horizon, bounded real daily data, cached stale/unavailable states | Weather contract/cache tests; real licensed provider/geocoding configuration remains operator work |
| A43–A45 | Repeatable-read report rows/source generations; CSV/XLSX/PDF; authorized durable jobs/private files; reviewed CSV all-or-nothing; permission recheck and public opt-in | Tests parse actual XLSX/CSV, check generated PDF bytes, filters/scopes/revocation/import commit; four-page synthetic PDF visual review passed. Production worker/storage remain operator checks |
| A46–A47 | Encrypted logical/physical/WAL adapters; isolated restore drill passed and preserved an acknowledged post-base score, two organizations/memberships, court/rule/result/audit state, files and runtime RLS | Local drill recorded in restore-validation.json; production/quarterly drills still required. Selective organization recovery is an operator-reviewed reconciliation, never automatic platform rewind |
| A48 | PostgreSQL-only application/tests; RLS nonowner runtime; court/scorer row locks and constraints | Run authoritative concurrency suite against PostgreSQL and collect results |
| A49–A50 | Responsive organizer/public/display/referee pages, safe areas, semantic controls, text alternatives, focus/contrast/reduced-motion design | Real-device and WCAG 2.2 AA review below; browser screenshot alone is insufficient |
| A51 | Recorded 25-game/500-distinct-viewer HTTP compact-poll workload passed local p95 ACK/live thresholds and correctness; backpressure/skipped ticks are recorded | This does not prove sustained 500 reads/second or production capacity; target-host deployment and p75 field LCP/INP remain unmeasured |
| A52 | Exact application credit in identity/organizer/referee/public/display/help and generated PDF | Check readable unobstructed credit on each required experience after branding changes |

## Automated evidence

The complete PostgreSQL suite passed **100 tests in 20.31 seconds**, including compact-live privacy/dynamic-participant/history-court/query-category bounds and public-metadata freshness. Score projection updates preserve the metadata digest; stage/visible announcement changes invalidate it while future invisible announcements do not. This also includes real row-lock races, stale/revoked takeover, shared-team/referee start conflicts, reviewed ranking/season-playoff/cross-pool tie handling, journal/projection recovery and revision predecessors, strict malformed rule-input rejection, narrow expired-throttle cleanup and sponsor display configuration with unsafe URL rejection. The suite includes 19 pure reporting/weather/encryption cases checking actual spreadsheet escaping/format output and complete large-source metadata, PDF column-group identity and bundled font/infinity coverage, numeric sort/selection bounds, weather schema/timezone validation, authenticated encryption/tamper rejection, private key file permissions, idempotent encrypted WAL archive/restore commands and sufficient source recovery settings. Completed local browser, HTTP workload, PDF and isolated restore evidence is attached below. Test code is in `tests/`; tests never use SQLite as a substitute.

The corrected isolated encrypted physical/WAL restore passed on October 5, 2026; [restore-validation.json](restore-validation.json) is the local evidence. It replayed a score committed after the physical base and checked organization/membership isolation, court/rule/result/audit state, files and runtime RLS. Isolated restoration took 1.795 seconds; the complete local drill took 17.502 seconds. Those values describe the recorded small local PostgreSQL 18 environment; production five-minute RPO/sixty-minute RTO remain unmeasured. Earlier socket/insufficient-parameter failures were corrected before the successful rerun. The source was returned to archive_mode off after validation.

Two local load measurements used 25 scoring games and 500 logical read-only viewers across two organizations for 20-second windows in one in-process Flask WSGI process, sharing a macOS host with the generator and PostgreSQL. Both served all 500 distinct viewers, used 25 writer/48 reader threads and a 30-connection database pool, and recorded zero request errors/lost Saved actions/committed duplicates/scope leaks or conflicting active courts. Their [original record](load-validation-before-optimization.json), [bulk-serialization record](load-validation-after-serialization.json) and [sanitized runtime-role query plans](query-plans.md) are retained.

| Local full-overview polling | Original | Bulk serialization | Proposed target |
| --- | --- | --- | --- |
| p95 durable score acknowledgment | 4.524 s | 5.604 s | ≤1 s |
| p95 public request, excluding generator queue | 1.737 s | 0.998 s | No separate target |
| p95 Saved-to-public committed version | 5.051 s | 3.620 s | ≤2 s |
| Acknowledged actions / public reads | 240 / 3,155 | 202 / 4,331 | Workload evidence |

Both proposed latency targets remained unmet in those in-process measurements. Generator queueing/backpressure skipped read/write ticks; five expected score observations were not seen before the revised run's drain ended. This does not prove 500 sustained simultaneous network connections. Warm, small-table query plans were submillisecond and suggest application/generator scheduling and connection contention need further profiling; they do not prove a root cause.

The [two-worker HTTP run with two-second polls](load-validation-http-two-workers.json) used real loopback Gunicorn with two workers/eight threads each and a separate generator process. It performed 500 initial full reads, then compact polling across the same 25 games/500 distinct viewers. All 500 actions were Saved; 100 duplicate retries added no duplicate event; 5,723 live reads and all 125,000 expected observations completed without request/correctness failures or skipped writer/viewer ticks. Its ACK p95 was 307.350 ms, but Saved-to-public p95 was 2,094.521 ms and missed two seconds. This prompted a shorter polling period.

The **[final one-second HTTP run](load-validation.json)** used two workers/eight threads each, 25 generator writer threads, 128 bounded reader threads and a maximum 30 database-pool connections per application process. PostgreSQL 18.6 ran locally with fsync/synchronous_commit enabled. The 20-second measurement plus delivery drain took 28.827 seconds. It served all **500 distinct viewers and 25 scoring games across two organizations**, accepted **479 Saved point actions**, processed **85 valid duplicate retries and 6,606 compact live reads**, and delivered all **119,750/119,750 expected committed-version observations**. Request errors, lost Saved actions, duplicate committed actions, scope leaks and public secret disclosures were zero; all 25 active courts remained uniquely occupied and journal/projections agreed.

| Final local HTTP metric | Measured | Proposed threshold |
| --- | --- | --- |
| p95 durable acknowledgment | **842.819 ms** | ≤1,000 ms; local p95 passed |
| p95 Saved-to-public visibility | **1,718.661 ms** | ≤2,000 ms; local p95 passed |
| p99 / maximum public visibility | 2,861.248 / 6,751.088 ms | Slow tails exceed two seconds |
| Expected observations delivered within two seconds | 97.9666% | Not all observations met two seconds |
| p95 compact HTTP read before generator queue | 1,267.990 ms | No separate proposed threshold |

**Backpressure limits accompany these passed p95 results:** one outstanding request per viewer/writer skipped **6,742 viewer ticks and 21 writer ticks**. Although one-second polling offered 500 reads/second, the run does **not** prove sustained 500 successful reads/second or unrestricted concurrency. Generator queue-inclusive read p95 was 3,743.718 ms. Initial full-read timing is recorded separately and is not browser LCP. Visibility is measured from the Saved response acknowledgment until that viewer receives the committed version or newer; a version already received by Saved has zero delay. This is a short local loopback baseline, not a soak test or production capacity claim. TLS/proxies/mobile radios, packet loss, geographic latency, field LCP/INP and real-device behavior remain unmeasured.

The [PDF visual review](report-pdf-review.md) inspected all four pages of an eight-row/twenty-column synthetic standings export. It fixed ambiguous wide-table row identity and missing Polish font glyphs, then verified readable metadata/policies, long names, literal escaped markup, accented names/infinity, tables/margins, exact credit and page numbers without clipping/overlap/boxes. [Review measurements](report-pdf-review.json) include the output checksum and preview dimensions. This is an artifact review, not real mobile PDF reader/printer/accessibility validation.

The [local browser review](browser-review.md) records Chrome 154/macOS observations for organizer setup/generation/publication, the ten-second QR hide, admission/takeover/Saved point, side switching, 320-pixel referee controls, desktop wake-lock/whistle playback state, finalized reports, live dashboard refresh, public privacy clearing and the dedicated presentation. An actual web/worker restart preserved the accepted 12–6 score, Court 1 and running elapsed timer on reload. This desktop/narrow viewport review does not establish physical iPhone/Android/TV behavior, audible venue playback or WCAG conformance; the real-device checklist below remains open.

## Real-device/browser review

- [ ] Record device, OS/browser version, portrait/landscape, network conditions and date for iPhone Safari, Android Chrome, desktop and TV/projector target.
- [ ] At 320 CSS pixels and 200% text scaling, verify no essential controls disappear/overlap; inspect safe-area insets, score touch spacing and orientation changes.
- [ ] QR hidden on open, reveal count starts at 10, repeated reveal resets, deadline hides at zero; app switch/navigation hides immediately and returning does not revive stale QR.
- [ ] Scan/admit, start, point/serve/timeout, completed set confirmation, undo at boundary, pause/resume, explicit end; check Saved vs Pending is visible and accurate.
- [ ] Disable network, queue actions, restore network, lose an acknowledgment, restart browser; confirm duplicates are reconciled. Takeover on second device fences delayed first-device writes and explains unrecoverable unsent first-device points.
- [ ] Start/resume asks wake lock; visible return reacquires; paused match keeps the request; end/exit/revocation releases. Unsupported/denied/manual lock/power-saving state displays accurate guidance.
- [ ] Unlock and test local whistle via a gesture; repeated taps prevent overlapping/stuck sound, respect hardware volume, and never mutate match state.
- [ ] Public team/division/stage/date/venue/court filtering and no-court label work. Change Public to Private while viewing and confirm clearing/denial of APIs, export and live refresh.
- [ ] Dedicated fullscreen shows selected safe widgets with no navigation/login/management/QR/scoring affordances; stale/offline state and exact credit remain readable.
- [ ] Keyboard and screen reader reach every form/control/modal; focus order, labels, errors, live score/status announcements and close/confirm behavior work without a mouse.
- [ ] Contrast, touch target size, noncolor state labels, reduced motion, theme, table alternatives and chart summaries meet the accessibility review.

## Recovery and deployment gates

- [ ] Confirm hosting/runtime stack, independent private durable storage and encrypted recovery storage/transport; grant distinct migration/runtime/backup roles.
- [ ] Apply migrations in one controlled release step; verify nonowner RLS and tenant scope on multiple pooled connections.
- [ ] Configure real email delivery, MFA key management/recovery, HTTPS/cookies/origins/log redaction; exercise invite/reset/revocation flows with test identities.
- [ ] Confirm licensed weather/map/geocoder provider and forecast attribution/limits or preserve explicitly unavailable integrations.
- [ ] Configure scheduled physical base backups, continuous WAL/PITR or equivalent managed service, file versions/snapshots, retention (proposed 30 days), monitoring and incident contacts.
- [ ] Complete isolated restore, compare accepted journal/state/rules/result revisions/audits/memberships/active courts/files and record measured RPO/RTO; repeat at least quarterly.
- [ ] Review selective organization reconciliation plan with scoped foreign keys and shared identities; preserve all unrelated organizations.
- [ ] Run target-host load baseline, document bottlenecks/error rates/data volumes and any unachieved latency/mobile targets. No contract capacity/uptime claim without measured evidence.
- [ ] Confirm event formats/ranking/forfeit/cross-pool formulas, approval policy, QR admission lifetime, backup retention/recovery targets and support/retention/deletion policy with the owner.

Scheduled report distribution, custom builders, player/team accounts, payments, native clients, additional formats, enterprise SSO and multiregion failover remain deferred as specified. No live account has been connected or public production deployment authorized by this build request.
