"""Verify/rebuild mutable score projections from immutable accepted journals."""
from copy import deepcopy
from sqlalchemy import select
from ..models import MatchEvent,ResultRevision
from ..common import APIError
from ..db import get_db


def journal_projection(match):
    db=get_db();events=list(db.scalars(select(MatchEvent).where(MatchEvent.match_id==match.id).order_by(MatchEvent.sequence)))
    if not events:return {'match_id':match.id,'events':0,'consistent':not bool(match.state),'rebuildable':False}
    latest=events[-1];state=latest.payload.get('after',latest.payload.get('state'))
    if state is None:raise APIError('This journal predates projection snapshots; use reviewed historical recovery.',409)
    snapshot=next((e.payload['rule_snapshot'] for e in reversed(events) if e.payload.get('rule_snapshot')),match.rule_snapshot)
    return {'match_id':match.id,'events':len(events),'last_sequence':latest.sequence,'match_version':match.version,
        'consistent':state==match.state,'rebuildable':True,'state':deepcopy(state),'rule_snapshot':deepcopy(snapshot),'lifecycle':latest.payload.get('lifecycle')}

def rebuild(match):
    projection=journal_projection(match)
    if not projection['rebuildable']:raise APIError('No accepted journal snapshot exists for this match.',409)
    match.state=projection['state']
    if projection.get('rule_snapshot'):match.rule_snapshot=projection['rule_snapshot']
    return projection
