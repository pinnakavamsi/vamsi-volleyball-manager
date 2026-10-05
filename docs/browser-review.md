# Local browser review

Reviewed October 5, 2026 against the running local application at `http://127.0.0.1:5050`, using Chrome 154 on macOS. Desktop observations used a 2048 x 974 CSS-pixel viewport; the referee review also used a 320 x 740 viewport. Narrow browser emulation is not a physical iPhone or Android test. All reviewed competition data was explicitly labeled demo data.

## Observed workflows

| Workflow | Observed result |
| --- | --- |
| Organizer setup | Created an isolated private draft, one division, two existing-team entries and a round-robin stage. Reviewed one generated fixture before committing it, validated the event, and published it with private visibility. |
| Game QR | Game dialog initially hid the QR. Show QR Code revealed it with a ten-second countdown; after expiry the QR content was hidden and its image removed. No raw grant was retained in the hidden link/image DOM. No QR or credential appears in the saved previews. |
| Delegated referee | Fresh admission showed explicit takeover. Taking control retained the central score; a point became Saved after acknowledgment. Switching displayed sides retained team identities. |
| Narrow referee layout | Both large point buttons, scores, timeout actions and application credit remained visible at 320 pixels with no horizontal overflow. Timeout labels were shortened while retaining full accessible labels. |
| Wake lock and whistle | Chrome reported foreground Screen awake. The gesture-triggered whistle playback promise succeeded and showed venue-volume guidance. This does not verify physical phone support or audible venue volume. |
| Application restart | After stopping and restarting the web service and worker, referee reload retrieved the current 12–6 score, Court 1 and continuing elapsed timer. Public and display views showed the same accepted score. |
| Report browser | Finalized Hudson Invitational match filters returned two official rows. Default columns showed teams, scope, court, time, status, readable set scorelines and winner; empty totals were absent and source details were available separately. |
| Organizer refresh | With the dashboard open, changing the isolated review event from Completed back to Published updated the active-event count from 1 to 2 and its event label without reloading the dashboard. The review event was restored to Published and Private. |
| Public privacy | Opened the isolated review event locally while Public, then changed it back to Private. Its already-open spectator view automatically cleared to Tournament unavailable, including removal of its fixtures and filters. The seeded public event was unaffected. |
| Public page | Showed persisted 12–6 live scores, event/team/division/stage/date/venue/court filters, official standings, venue information and demo attribution. No management, QR or scoring controls were present. |
| Presentation | Observed the successive live/standings, upcoming/announcement and weather pages in the saved five-widget demo. Legacy Schedule normalized to Up next without an empty rotation page. One standings division appears per cycle with its name and finalized-result label. Two normal widgets fit on a desktop page, narrow screens show one, and wide widgets occupy their own page. Observed desktop panels ended above the exact credit at the viewport bottom. No navigation, login, management, QR or scoring controls were present. |

Local previews are in `instance/screenshots/dashboard.jpg`, `instance/screenshots/referee-mobile.jpg` and `instance/screenshots/display.jpg`. Generated report PDF review has a separate [measured record](report-pdf-review.md).

## Remaining review

The [release checklist](release-checklist.md) retains physical iPhone Safari/Android Chrome, TV/projector, assistive-technology, 200% text scaling, device safe-area/orientation, weak-network/offline queue and hardware sound/wake-lock checks. QR background/navigation behavior was implemented and reviewed in source; a physical-device switching/scan test remains outstanding. These local observations do not establish WCAG conformance, mobile field LCP/INP, production capacity, live provider operation or a recovery service guarantee.
