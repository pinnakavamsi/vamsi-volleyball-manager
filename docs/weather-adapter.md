# Address book and venue weather

Organizers can create a tournament without its venue or courts and add them later from **Venues & courts**. Venues are saved in the organization's address book and may be attached to more than one tournament. Courts belong to a saved venue. Linking a saved venue reuses its courts. A venue cannot be detached after a game has used one of its courts, or while a fixture explicitly names that venue. New game assignments require a venue attached to the tournament; an existing court assignment remains available for recovery.

The address form does not require latitude or longitude. It searches saved addresses and, when configured, street addresses through Photon. The organizer chooses the matching suggestion. The server validates a signed selection tied to the organization and user, valid for 15 minutes, and stores the provider coordinates. Editing the text after a selection requires choosing a new suggestion. A manually entered address can still be saved; weather accurately remains unavailable until its location is resolved. Saved addresses without a resolved location do not acquire verified coordinates when reused.

Keep an explicit IANA timezone on each venue. New tournament venues inherit the tournament timezone unless the organizer chooses another one; copying a saved address can reuse its timezone. Photon does not supply a venue timezone. If the selected address is outside the event's timezone, choose the venue's correct timezone before saving. The forecast request uses the saved venue timezone so event-day boundaries and labels stay consistent.

## Provider configuration

Both external services are **disabled by default**. Saved addresses and tournament administration work without them. The owner must confirm the deployment's use qualifies for the selected provider terms before setting the confirmation flags. These nonsecret variables can be supplied through the application container's environment; do not put commercial credentials in source control.

For a confirmed noncommercial community/demo deployment:

```dotenv
GEOCODING_PROVIDER=photon
GEOCODING_PROVIDER_URL=https://photon.komoot.io/api/
GEOCODING_LICENSE_CONFIRMED=true
WEATHER_PROVIDER_TYPE=open-meteo
WEATHER_PROVIDER_URL=https://api.open-meteo.com/v1/forecast
WEATHER_PROVIDER_NAME=Open-Meteo
WEATHER_LICENSE_CONFIRMED=true
```

The [Photon project](https://github.com/komoot/photon#demo-server) allows reasonable use of its demonstration server and may throttle or ban extensive usage. It provides no availability guarantee. Use a licensed hosted or separately operated Photon service as volume grows; configure its trusted HTTPS API endpoint. Attribution identifies Photon and OpenStreetMap contributors. This app deliberately does not use the public Nominatim service for autocomplete.

The [Open-Meteo terms](https://open-meteo.com/en/terms) restrict the free hosted API to noncommercial uses, with limits below 10,000 calls/day, 5,000/hour and 600/minute. Subscriptions, advertising and commercial products require an appropriate paid service. Its [data licence](https://open-meteo.com/en/licence) requires attribution. For licensed commercial access, use:

```dotenv
WEATHER_PROVIDER_TYPE=open-meteo
WEATHER_PROVIDER_URL=https://customer-api.open-meteo.com/v1/forecast
WEATHER_PROVIDER_NAME=Open-Meteo
WEATHER_LICENSE_CONFIRMED=true
WEATHER_PROVIDER_API_KEY=YOUR_PRIVATE_CUSTOMER_KEY
```

An API key is sent only from the server to the official customer endpoint. The native adapter rejects a key on the free endpoint and requires one on the customer endpoint. Geocoding usage must be configured under its own service terms.

## Request limits and forecast behavior

Address search requires at least three characters and accepts at most 200. It limits each authenticated user to 30 searches/minute, starts at most one remote lookup/second per app worker, caches identical queries for ten minutes and retains at most 256 cache entries. Requests use a fixed configured HTTPS URL, three-second timeout, no redirects and a streaming 200 KB response cap. A failed lookup is cached briefly; saved address matches remain available. Queries and signed selections are not recorded in audit messages. Search results are limited and deduplicated. Saved records follow organization and assigned tournament authorization.

The native adapter requests the [Open-Meteo daily forecast variables](https://open-meteo.com/en/docs): weather code, daily high/low Celsius temperature, maximum precipitation probability and maximum 10 m wind speed in km/h, with a horizon of up to 16 days. Returned units, dates and values are checked. It presents actual upcoming event dates only, at most seven per venue. A league uses upcoming scheduled fixture dates. It never substitutes current weather for a future event. Beyond the actual horizon, the response says **Forecast available closer to the tournament**.

Weather has a three-second timeout, one retry and a streaming 100 KB response cap. Valid forecasts are cached for 30 minutes per organization/venue/provider; a failed update may display matching cache up to 12 hours old with a stale label. Address, coordinates and timezone changes invalidate that venue's cache. A server-generated location fingerprint and a locked location recheck prevent a request for an old address from restoring stale data after an edit. Fahrenheit is converted from cached Celsius values. Every available forecast retains its source, attribution and fetch time. Missing configuration, unresolved addresses and provider failures return an unavailable state and do not affect scoring.

## Application API

- `GET /api/v1/addresses/search?q=...&tournament_id=...` returns `{items,status,message,attribution}`. Each item has `source`, `label`, `address`, `resolved` and `selection_token`; saved items also have a venue `id`. The optional event identifier is authorized before any provider call.
- Venue POST/PATCH accepts `address_selection_token` or `address_id` (a saved venue identifier), alongside the normal name/address/timezone/indoor fields. Never send provider coordinates from the browser. Provenance and location verification are server-owned. Availability settings may contain opening hours, closures, layout URL and reservation minutes; do not echo address provenance as editable settings.
- `GET /api/v1/tournaments/{id}/venues` returns attached `items` with their `courts`, plus `available` saved venues.
- `POST /api/v1/tournaments/{id}/venues` takes `{venue_id}` to attach a saved venue, or `{venue:{...},courts:[{name,...}]}` to create and attach a venue with optional courts in one transaction.
- `POST /api/v1/tournaments/{id}/venues/{venue_id}/courts` adds a court later. `DELETE` on the corresponding venue link detaches only an unused venue.
- `GET /api/v1/courts?tournament_id=...` limits eligible inventory to that event's venues while retaining normal operational availability labels.

## Other licensed weather adapters

Use `WEATHER_PROVIDER_TYPE=normalized`, an HTTPS `WEATHER_PROVIDER_URL`, `WEATHER_PROVIDER_NAME` and `WEATHER_LICENSE_CONFIRMED=true` for a different installed adapter. An optional `WEATHER_PROVIDER_TOKEN` is a server-side bearer credential. The endpoint receives latitude, longitude and timezone and must return this schema:

```json
{
  "timezone": "America/New_York",
  "forecast_start": "2026-10-05",
  "forecast_end": "2026-10-11",
  "updated_at": "2026-10-05T09:00:00Z",
  "temperature_unit": "C",
  "wind_unit": "km/h",
  "attribution": "Licensed provider attribution required by your agreement",
  "days": [
    {"date":"2026-10-05","condition":"Provider condition description",
     "temperature_high":21,"temperature_low":12,"rain_probability":30,"wind_speed":14}
  ]
}
```

This is contract documentation, never a production forecast. `WEATHER_ADAPTER` may be injected as a callable receiving a venue and returning that schema for isolated tests or an installed adapter. `GEOCODING_ADAPTER` may similarly return a Photon-shaped GeoJSON feature collection for an isolated fixture. Injected test adapters never enable an unconfirmed external service. Provider strings must be plain text, values finite, dates unique, rain probability 0–100 and temperature high at least low; the normalized response's timezone must match the venue.
