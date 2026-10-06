import uuid
from datetime import datetime, timezone, date
from flask import g, request
from sqlalchemy import inspect
from .db import get_db
from .models import Audit, Outbox

class APIError(Exception):
    def __init__(self,message,status=400,code='validation',details=None):
        self.message,self.status,self.code,self.details=message,status,code,details
        super().__init__(message)
def now(): return datetime.now(timezone.utc)
def uuid_str(): return str(uuid.uuid4())
def iso(value): return value.isoformat() if value else None
def body():
    data=request.get_json(silent=True)
    if not isinstance(data,dict): raise APIError('Send a JSON object.')
    return data

def serialize(model):
    if model is None: return None
    sensitive={'password_hash','token_hash','mfa_secret','mfa_recovery_hashes','csrf_token','receipt'}
    result={}
    for col in inspect(type(model)).columns:
        if col.name in sensitive: continue
        value=getattr(model,col.name)
        result[col.name]=iso(value) if isinstance(value,(datetime,date)) else value
    return result

def audit(action,entity_type,entity_id,before=None,after=None,reason=None,organization_id=None):
    organization_id=organization_id or getattr(getattr(g,'organization',None),'id',None) or get_db().info.get('organization_id')
    user=getattr(g,'user',None)
    scorer=getattr(g,'scorer',None)
    row=Audit(organization_id=organization_id,actor_id=user.id if user else None,
        session_id=scorer.id if scorer else None,actor_type=('platform_admin' if user.platform_admin else 'organizer') if user else ('delegated_referee' if scorer else 'system'),
        action=action,entity_type=entity_type,entity_id=entity_id,before=before,after=after,reason=reason)
    get_db().add(row)
    return row

def emit(match,kind):
    row=Outbox(organization_id=match.organization_id,match_id=match.id,tournament_id=match.tournament_id,
        kind=kind,version=match.version,payload={'match_id':match.id,'version':match.version,'generation':match.generation})
    get_db().add(row)
    return row
