# Weather adapter contract

Weather is disabled by default. The build does not select or claim a live commercial license for Open Meteo or another provider. Address geocoding is also not configured: organizers can enter/correct coordinates and mark `venue.settings.coordinates_verified=true` after checking the actual venue. Keep the IANA timezone on the venue. Configure a licensed geocoder/provider separately if automated address resolution is desired.

Enable a real server-side adapter with `WEATHER_PROVIDER_URL` (HTTPS), `WEATHER_PROVIDER_NAME` and `WEATHER_LICENSE_CONFIRMED=true`. An optional `WEATHER_PROVIDER_TOKEN` is sent as a bearer credential only from the server. The endpoint receives `latitude`, `longitude` and `timezone` query parameters and must provide this normalized JSON. A provider-specific service can translate its original response into this format:

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

The example is contract documentation; it is never served as production weather. `WEATHER_ADAPTER` can be injected as a callable for isolated tests or an installed adapter; it receives a venue and returns the same schema. Provider names, conditions and attribution must be plain text. Values must be finite, dates unique and inside the declared actual horizon, rain probability 0–100 and temperature high at least low. A response's timezone must match the venue. The adapter makes bounded requests, retries transient failures once, caches success for 30 minutes in scoped PostgreSQL and can display cache up to 12 hours old with a visible stale label. It rejects excessive payloads and never fetches a user-provided URL.

Only actual event days inside the returned horizon appear, at most seven per venue. A months-long league uses upcoming scheduled fixture dates; a tournament uses upcoming local calendar dates within its event range. No current forecast substitutes for a future event date. Beyond the horizon, the response says Forecast available closer to the tournament. Missing licensing/configuration/coordinates or provider failure returns an accurate unavailable state. Fahrenheit is converted from the cached Celsius values; wind units and update/attribution remain explicit. Weather calls use separate endpoints and cannot prevent scoring transactions.
