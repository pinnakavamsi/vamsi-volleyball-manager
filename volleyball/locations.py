"""Organization address book and bounded, server-side address suggestions."""
from collections import OrderedDict
from threading import Lock
import json
import math
import time
from urllib.parse import urlparse

import httpx
from flask import Blueprint, current_app, g, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import or_, select

from .auth import authorize_tournament, require_org, scoped_tournament_ids, valid_id
from .common import APIError, iso
from .db import get_db
from .models import Court, CourtAssignment, Match, Tournament, TournamentVenue, Venue

bp = Blueprint('locations', __name__, url_prefix='/api/v1')
ATTRIBUTION = 'Address data © OpenStreetMap contributors (ODbL); search by Photon.'
BUSY_MESSAGE = 'Address search is busy. Suggestions will retry shortly; saved addresses remain available.'


def tournament_venue_ids(tournament):
    """Use selected venues, retaining legacy game venues when selection is empty."""
    settings = tournament.settings or {}
    if settings.get('venue_ids'):
        return list(settings['venue_ids'])
    db = get_db()
    ids = list(db.scalars(select(TournamentVenue.venue_id).where(
        TournamentVenue.organization_id == tournament.organization_id,
        TournamentVenue.tournament_id == tournament.id, TournamentVenue.active.is_(True))))
    ids += list(db.scalars(select(Court.venue_id).join(CourtAssignment, CourtAssignment.court_id == Court.id)
        .join(Match, Match.id == CourtAssignment.match_id).where(
            Court.organization_id == tournament.organization_id,
            CourtAssignment.organization_id == tournament.organization_id,
            Match.organization_id == tournament.organization_id, Match.tournament_id == tournament.id).distinct()))
    return list(dict.fromkeys(ids))


def validate_venue_ids(values):
    if not isinstance(values, list) or len(values) > 50:
        raise APIError('Choose at most 50 tournament venues.')
    return list(dict.fromkeys(valid_id(value) for value in values))


def guard_venue_removal(tournament, removed_ids):
    if not removed_ids:
        return
    db = get_db()
    assignment = db.scalar(select(CourtAssignment.id).join(Court, Court.id == CourtAssignment.court_id)
        .join(Match, Match.id == CourtAssignment.match_id).where(
            Match.organization_id == tournament.organization_id, Match.tournament_id == tournament.id,
            Court.venue_id.in_(removed_ids)).limit(1))
    planned = db.scalar(select(Match.id).where(Match.organization_id == tournament.organization_id,
        Match.tournament_id == tournament.id, Match.settings['venue_id'].astext.in_(removed_ids)).limit(1))
    if assignment or planned:
        raise APIError('This venue is used by a planned or previously assigned game. Keep it attached to preserve the schedule and game history.',
                       409, 'venue_in_use')


def visible_venue_query():
    query = select(Venue).where(Venue.organization_id == g.organization.id)
    scopes = scoped_tournament_ids()
    if scopes:
        linked = select(TournamentVenue.venue_id).where(TournamentVenue.organization_id == g.organization.id,
            TournamentVenue.tournament_id.in_(scopes), TournamentVenue.active.is_(True))
        historical = select(Court.venue_id).join(CourtAssignment, CourtAssignment.court_id == Court.id)
        historical = historical.join(Match, Match.id == CourtAssignment.match_id).where(
            Match.organization_id == g.organization.id, Match.tournament_id.in_(scopes))
        # Some older events have JSON associations predating the relationship table.
        events = get_db().scalars(select(Tournament).where(Tournament.organization_id == g.organization.id,
            Tournament.id.in_(scopes))).all()
        legacy_ids = [vid for event in events for vid in (event.settings or {}).get('venue_ids', [])]
        query = query.where(or_(Venue.id.in_(linked), Venue.id.in_(historical), Venue.id.in_(legacy_ids)))
    return query


def saved_address(venue_id):
    venue = get_db().scalar(visible_venue_query().where(Venue.id == valid_id(venue_id)))
    if not venue:
        raise APIError('Saved address is outside your organization or assigned tournaments.', 404, 'not_found')
    return venue


def guard_shared_venue_change(venue):
    scopes = scoped_tournament_ids()
    if not scopes:
        return
    saved_address(venue.id)
    linked = get_db().scalar(select(TournamentVenue.id).where(TournamentVenue.organization_id == g.organization.id,
        TournamentVenue.venue_id == venue.id, TournamentVenue.active.is_(True), TournamentVenue.tournament_id.not_in(scopes)).limit(1))
    historical = get_db().scalar(select(CourtAssignment.id).join(Court, Court.id == CourtAssignment.court_id)
        .join(Match, Match.id == CourtAssignment.match_id).where(Match.organization_id == g.organization.id,
            Match.tournament_id.not_in(scopes), Court.venue_id == venue.id).limit(1))
    configured = get_db().scalar(select(Tournament.id).where(Tournament.organization_id == g.organization.id,
        Tournament.id.not_in(scopes), Tournament.settings['venue_ids'].contains([venue.id])).limit(1))
    if linked or historical or configured:
        raise APIError('This saved venue is shared with tournaments outside your assignment. An organization administrator must update it.',
                       403, 'forbidden')


def _signer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='volleyball-address-selection-v1')


def _token(data):
    return _signer().dumps({**data, 'organization_id': g.organization.id, 'user_id': g.user.id})


def resolve_selection(token=None, address_id=None):
    if token and address_id:
        raise APIError('Choose one saved address or one address suggestion.')
    if address_id:
        payload = {'source': 'saved', 'id': address_id}
    else:
        if not isinstance(token, str) or len(token) > 5000:
            raise APIError('Choose an address suggestion again.')
        try:
            payload = _signer().loads(token, max_age=900)
        except (BadSignature, SignatureExpired):
            raise APIError('This address suggestion expired or changed. Search and choose it again.', 400, 'address_selection_expired')
        if (not isinstance(payload, dict) or payload.get('organization_id') != g.organization.id
                or payload.get('user_id') != g.user.id):
            raise APIError('Choose an address suggestion from this organization.', 403, 'forbidden')
    if payload.get('source') == 'saved':
        venue = saved_address(payload.get('id'))
        verified = bool((venue.settings or {}).get('coordinates_verified') and venue.latitude is not None and venue.longitude is not None
                        and math.isfinite(venue.latitude) and math.isfinite(venue.longitude)
                        and -90 <= venue.latitude <= 90 and -180 <= venue.longitude <= 180)
        return {'address': venue.address or '', 'latitude': venue.latitude if verified else None,
                'longitude': venue.longitude if verified else None, 'timezone': venue.timezone,
                'verified': verified, 'source': 'saved', 'attribution': (venue.settings or {}).get('address_attribution')}
    if payload.get('source') != 'photon':
        raise APIError('Choose a valid address suggestion.')
    try:
        latitude, longitude = float(payload['latitude']), float(payload['longitude'])
        address = payload['address']
        if (not math.isfinite(latitude) or not math.isfinite(longitude) or not -90 <= latitude <= 90
                or not -180 <= longitude <= 180 or not isinstance(address, str) or not address or len(address) > 1000):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise APIError('Choose a valid address suggestion.')
    return {'address': address, 'latitude': latitude, 'longitude': longitude, 'verified': True,
            'source': 'photon', 'attribution': ATTRIBUTION}


def geocoder_configured():
    return bool(current_app.config.get('GEOCODING_ADAPTER') or
        (current_app.config.get('GEOCODING_PROVIDER') == 'photon'
         and current_app.config.get('GEOCODING_PROVIDER_URL') and current_app.config.get('GEOCODING_LICENSE_CONFIRMED')))


def _state():
    return current_app.extensions.setdefault('address_suggestions', {'cache': OrderedDict(), 'lock': Lock(), 'next_request': 0})


def bounded_json_get(endpoint, *, params, headers=None, max_bytes=200000):
    """Read only a bounded response, including after decompression."""
    chunks, size = [], 0
    with httpx.stream('GET', endpoint, params=params, headers=headers or {}, timeout=httpx.Timeout(3, connect=2),
                      follow_redirects=False) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes(chunk_size=8192):
            size += len(chunk)
            if size > max_bytes:
                raise ValueError('Provider response exceeded its limit.')
            chunks.append(chunk)
    return json.loads(b''.join(chunks))


def _photon_items(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('features'), list):
        raise ValueError()
    items = []
    for feature in payload['features'][:8]:
        try:
            geometry, props = feature['geometry'], feature['properties']
            if geometry.get('type') != 'Point' or len(geometry['coordinates']) != 2:
                continue
            longitude, latitude = geometry['coordinates']
            if (isinstance(latitude, bool) or isinstance(longitude, bool) or not isinstance(latitude, (int, float))
                    or not isinstance(longitude, (int, float)) or not math.isfinite(latitude) or not math.isfinite(longitude)
                    or not -90 <= latitude <= 90 or not -180 <= longitude <= 180):
                continue
            def part(key):
                value = props.get(key, '')
                return value.strip() if isinstance(value, str) and len(value) <= 200 else ''
            street = ' '.join(filter(None, [part('housenumber'), part('street')]))
            locality = ' '.join(filter(None, [part('postcode'), part('city') or part('town') or part('village')]))
            parts = [part('name'), street, locality, part('state'), part('country')]
            address = ', '.join(dict.fromkeys(value for value in parts if value))
            if not address or len(address) > 1000:
                continue
            items.append({'source': 'photon', 'label': address, 'address': address, 'latitude': latitude, 'longitude': longitude})
        except (KeyError, TypeError, AttributeError, ValueError):
            continue
    return items


def _provider_items(query):
    state = _state()
    adapter = current_app.config.get('GEOCODING_ADAPTER')
    endpoint = current_app.config.get('GEOCODING_PROVIDER_URL', '')
    cache_key = (endpoint, id(adapter), query.casefold())
    tick = time.monotonic()
    with state['lock']:
        cached = state['cache'].get(cache_key)
        if cached and cached[0] > tick:
            state['cache'].move_to_end(cache_key)
            return cached[1], cached[2]
        if state['next_request'] > tick:
            return [], BUSY_MESSAGE
        state['next_request'] = tick + 1
    try:
        if adapter:
            payload = adapter(query)
        else:
            parsed = urlparse(endpoint)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError()
            payload = bounded_json_get(endpoint, params={'q': query, 'limit': 5, 'lang': 'en'},
                headers={'User-Agent': 'Volleyball-Manager/1.0 (server address lookup)'})
        items, message = _photon_items(payload), None
    except (httpx.HTTPError, ValueError, TypeError, RuntimeError):
        items, message = [], 'Address suggestions are temporarily unavailable. You can enter an address and save it for later.'
    with state['lock']:
        state['cache'][cache_key] = (tick + (600 if message is None else 30), items, message)
        state['cache'].move_to_end(cache_key)
        while len(state['cache']) > 256:
            state['cache'].popitem(last=False)
    return items, message


@bp.get('/addresses/search')
@require_org('read')
def search_addresses():
    query = ' '.join(request.args.get('q', '').split())
    if len(query) < 3:
        return {'items': [], 'status': 'query_too_short', 'message': 'Type at least 3 characters to search saved addresses and address suggestions.', 'attribution': None}
    if len(query) > 200:
        raise APIError('Keep address searches under 200 characters.')
    if request.args.get('tournament_id'):
        event = get_db().scalar(select(Tournament).where(Tournament.id == valid_id(request.args['tournament_id']),
            Tournament.organization_id == g.organization.id))
        authorize_tournament(event)
    from .auth import _throttle
    _throttle('address_search_limit', f'{g.organization.id}:{g.user.id}', 30, 1)
    escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    venues = get_db().scalars(visible_venue_query().where(or_(Venue.address.ilike('%' + escaped + '%', escape='\\'),
        Venue.name.ilike('%' + escaped + '%', escape='\\'))).order_by(Venue.name).limit(8)).all()
    items = [{'id': v.id, 'source': 'saved', 'label': f'{v.name} · {v.address}' if v.address else v.name,
              'address': v.address or '', 'timezone': v.timezone,
              'resolved': bool((v.settings or {}).get('coordinates_verified') and v.latitude is not None and v.longitude is not None),
              'selection_token': _token({'source': 'saved', 'id': v.id})} for v in venues]
    message, attribution = None, None
    configured = geocoder_configured()
    if configured:
        suggestions, message = _provider_items(query)
        seen = {item['address'].casefold() for item in items}
        for item in suggestions:
            if item['address'].casefold() in seen:
                continue
            seen.add(item['address'].casefold())
            items.append({'source': item['source'], 'label': item['label'], 'address': item['address'], 'resolved': True,
                          'selection_token': _token(item)})
        attribution = ATTRIBUTION
    else:
        message = 'Search saved addresses, or enter a new address. Map suggestions and address-based weather will be available once a provider is configured.'
    return {'items': items[:12], 'status': 'busy' if message == BUSY_MESSAGE else 'available' if configured and not message else 'saved_only',
            'message': message, 'attribution': attribution, 'retry_after': 1 if message == BUSY_MESSAGE else None}
