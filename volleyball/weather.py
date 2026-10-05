"""Replaceable licensed daily-weather adapter; unavailable unless configured.

Provider contract is documented in docs/weather-adapter.md. No demo forecasts.
"""
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
import math
import time
import httpx
from flask import Blueprint, current_app, g, request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from .auth import require_org
from .common import APIError, iso, now
from .db import get_db
from .models import Organization, Venue, Court, CourtAssignment, Match, WeatherCache

bp=Blueprint('weather',__name__,url_prefix='/api/v1')


class WeatherUnavailable(Exception): pass


def provider_configured():
    config=current_app.config
    return bool(config.get('WEATHER_ADAPTER') or (config.get('WEATHER_PROVIDER_URL') and config.get('WEATHER_PROVIDER_NAME')
        and config.get('WEATHER_LICENSE_CONFIRMED',False)))


def validate_provider_payload(payload,venue):
    if not isinstance(payload,dict) or payload.get('timezone')!=venue.timezone:
        raise WeatherUnavailable('Provider timezone did not match the venue.')
    try:
        begin=date.fromisoformat(payload['forecast_start']);finish=date.fromisoformat(payload['forecast_end'])
        fetched=datetime.fromisoformat(payload['updated_at'].replace('Z','+00:00'))
        if fetched.tzinfo is None or begin>finish or (finish-begin).days>31: raise ValueError()
        if fetched>now()+timedelta(minutes=5) or fetched<now()-timedelta(days=2): raise ValueError()
        if payload.get('temperature_unit')!='C' or payload.get('wind_unit')!='km/h': raise ValueError()
        rows=payload['days'];seen=set();clean=[]
        if not isinstance(rows,list) or len(rows)>32: raise ValueError()
        for row in rows:
            day=date.fromisoformat(row['date'])
            if day in seen or not begin<=day<=finish: raise ValueError()
            seen.add(day)
            values={key:float(row[key]) for key in ('temperature_high','temperature_low','rain_probability','wind_speed')}
            if not all(math.isfinite(v) for v in values.values()): raise ValueError()
            if not 0<=values['rain_probability']<=100 or values['wind_speed']<0 or values['temperature_low']>values['temperature_high']: raise ValueError()
            condition=str(row['condition'])
            if len(condition)>100 or '<' in condition or '>' in condition: raise ValueError()
            clean.append({'date':day.isoformat(),'condition':condition,**values})
        attribution=str(payload.get('attribution',''))
        if not attribution or len(attribution)>500 or '<' in attribution or '>' in attribution: raise ValueError()
        return {'timezone':venue.timezone,'forecast_start':begin.isoformat(),'forecast_end':finish.isoformat(),
                'updated_at':fetched.isoformat(),'temperature_unit':'C','wind_unit':'km/h','attribution':attribution,'days':clean}
    except (KeyError,ValueError,TypeError,AttributeError): raise WeatherUnavailable('Weather provider returned incomplete daily data.')


def fetch_provider(venue):
    adapter=current_app.config.get('WEATHER_ADAPTER')
    if adapter:
        try: return validate_provider_payload(adapter(venue),venue)
        except WeatherUnavailable: raise
        except Exception: raise WeatherUnavailable('Weather provider unavailable.')
    endpoint=current_app.config['WEATHER_PROVIDER_URL']
    parsed=urlparse(endpoint)
    if parsed.scheme!='https' and not current_app.testing:
        raise WeatherUnavailable('Weather provider requires HTTPS.')
    if parsed.username or parsed.password or not parsed.hostname: raise WeatherUnavailable('Invalid server weather configuration.')
    headers={}
    if current_app.config.get('WEATHER_PROVIDER_TOKEN'): headers['Authorization']='Bearer '+current_app.config['WEATHER_PROVIDER_TOKEN']
    for attempt in range(2):
        try:
            response=httpx.get(endpoint,params={'latitude':venue.latitude,'longitude':venue.longitude,'timezone':venue.timezone},
                               headers=headers,timeout=httpx.Timeout(3,connect=2),follow_redirects=False)
            if response.status_code in {429,500,502,503,504} and attempt==0:
                time.sleep(.1);continue
            response.raise_for_status()
            if len(response.content)>100000: raise WeatherUnavailable('Weather provider response exceeded its limit.')
            return validate_provider_payload(response.json(),venue)
        except WeatherUnavailable: raise
        except (httpx.HTTPError,ValueError):
            if attempt==0: time.sleep(.1);continue
            raise WeatherUnavailable('Weather provider unavailable.')
    raise WeatherUnavailable('Weather provider unavailable.')


def event_days(tournament,venue):
    local_today=now().astimezone(ZoneInfo(venue.timezone)).date()
    start=max(local_today,tournament.start_date)
    if tournament.competition_type=='league':
        court_ids=list(get_db().scalars(select(Court.id).where(Court.organization_id==tournament.organization_id,Court.venue_id==venue.id)))
        assigned_ids=list(get_db().scalars(select(CourtAssignment.match_id).where(CourtAssignment.organization_id==tournament.organization_id,CourtAssignment.court_id.in_(court_ids))))
        fixtures=list(get_db().scalars(select(Match).where(Match.organization_id==tournament.organization_id,Match.tournament_id==tournament.id,Match.planned_at.is_not(None))))
        dates={m.planned_at.astimezone(ZoneInfo(venue.timezone)).date() for m in fixtures
               if (m.id in assigned_ids or m.settings.get('venue_id')==venue.id or len((tournament.settings or {}).get('venue_ids',[]))==1)}
        return sorted(day for day in dates if start<=day<=tournament.end_date)
    # Bounded to a year for long events; no entire multi-year calendar response.
    finish=min(tournament.end_date,start+timedelta(days=366))
    return [start+timedelta(days=i) for i in range(max(0,(finish-start).days+1))]


def venue_weather(tournament,venue,units):
    base={'venue':{'id':venue.id,'name':venue.name,'address':venue.address},'timezone':venue.timezone,
          'temperature_unit':'F' if units=='fahrenheit' else 'C','wind_unit':'km/h','days':[],'updated_at':None,
          'provider':current_app.config.get('WEATHER_PROVIDER_NAME'),'attribution':None}
    if not provider_configured(): return {**base,'status':'unavailable','message':'Weather is unavailable until a licensed provider is configured.'}
    relevant_days=event_days(tournament,venue)
    if not relevant_days:
        return {**base,'status':'unavailable','message':'No upcoming scheduled fixture dates at this venue.' if tournament.competition_type=='league' else 'No upcoming tournament dates.'}
    if venue.latitude is None or venue.longitude is None or not -90<=venue.latitude<=90 or not -180<=venue.longitude<=180:
        return {**base,'status':'unavailable','message':'Set and verify the venue map coordinates.'}
    if not (venue.settings or {}).get('coordinates_verified',False):
        return {**base,'status':'unavailable','message':'An organizer must verify the venue map coordinates before requesting weather.'}
    provider=current_app.config.get('WEATHER_PROVIDER_NAME','configured-adapter')
    cache=get_db().scalar(select(WeatherCache).where(WeatherCache.venue_id==venue.id,WeatherCache.provider==provider,WeatherCache.organization_id==venue.organization_id))
    stale=False
    if not cache or cache.expires_at<=now():
        try:
            payload=fetch_provider(venue);fetched=now()
            statement=insert(WeatherCache).values(organization_id=venue.organization_id,venue_id=venue.id,provider=provider,payload=payload,
                        fetched_at=fetched,expires_at=fetched+timedelta(minutes=30))
            statement=statement.on_conflict_do_update(index_elements=['venue_id','provider'],set_={'payload':payload,'fetched_at':fetched,
                        'expires_at':fetched+timedelta(minutes=30),'updated_at':fetched})
            get_db().execute(statement);get_db().commit()
            cache=get_db().scalar(select(WeatherCache).where(WeatherCache.venue_id==venue.id,WeatherCache.provider==provider).execution_options(populate_existing=True))
        except WeatherUnavailable:
            if not cache or cache.fetched_at<now()-timedelta(hours=12):
                return {**base,'status':'unavailable','message':'Weather provider unavailable. Core tournament operations remain available.'}
            stale=True
    payload=cache.payload
    relevant=set(day.isoformat() for day in relevant_days)
    selected=sorted([dict(row) for row in payload['days'] if row['date'] in relevant],key=lambda r:r['date'])[:7]
    if units=='fahrenheit':
        for row in selected:
            for key in ('temperature_high','temperature_low'): row[key]=round(row[key]*9/5+32,1)
    return {**base,'provider':provider,'status':'stale' if stale else 'available' if selected else 'outside_forecast_window',
            'message':'Cached forecast; provider update failed.' if stale else None if selected else 'Forecast available closer to the tournament.',
            'days':selected,'updated_at':payload['updated_at'],'cached_at':iso(cache.fetched_at),'attribution':payload['attribution'],
            'forecast_start':payload['forecast_start'],'forecast_end':payload['forecast_end']}


def tournament_weather(tournament):
    units=request.args.get('units','celsius')
    if units not in {'celsius','fahrenheit'}: raise APIError('Weather units must be celsius or fahrenheit.')
    venue_ids=(tournament.settings or {}).get('venue_ids',[])
    if not venue_ids:
        courts=select(Court.venue_id).join(CourtAssignment,CourtAssignment.court_id==Court.id).join(Match,Match.id==CourtAssignment.match_id).where(
            Match.organization_id==tournament.organization_id,Match.tournament_id==tournament.id)
        venue_ids=list(get_db().scalars(courts.distinct()))
    venues=list(get_db().scalars(select(Venue).where(Venue.organization_id==tournament.organization_id,Venue.id.in_(venue_ids))))
    if request.args.get('venue_id'): venues=[v for v in venues if v.id==request.args['venue_id']]
    if not venues: return {'items':[],'status':'unavailable','message':'No venue is configured for this tournament.','generated_at':iso(now())}
    return {'items':[venue_weather(tournament,v,units) for v in venues],'generated_at':iso(now())}


@bp.get('/tournaments/<tournament_id>/weather')
@require_org('read')
def private_weather(tournament_id):
    from .reports import scoped
    from .models import Tournament
    return tournament_weather(scoped(Tournament,tournament_id))


@bp.get('/public/<slug>/weather')
def public_weather(slug):
    from .competition import public_tournament
    tournament=public_tournament(slug);response=tournament_weather(tournament)
    get_db().refresh(tournament)
    if tournament.visibility!='public' or tournament.status=='draft': raise APIError('Tournament is private.',404,'not_found')
    return response
