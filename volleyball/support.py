"""Organization support cases and the restricted platform support queue."""
from flask import Blueprint, g, request
from sqlalchemy import func, select

from .auth import require_org, require_platform_admin, valid_id
from .common import APIError, audit, body, iso, now
from .db import get_db, set_scope
from .models import Organization, SupportCase, SupportMessage, User

bp = Blueprint('support', __name__, url_prefix='/api/v1')
STATUSES = {'open', 'in_progress', 'waiting_on_organization', 'resolved', 'closed'}
CATEGORIES = {'account_access', 'tournament_setup', 'scoring', 'other'}
PAGE_SIZE = 50


def _page():
    try:
        page = int(request.args.get('page', '1'))
    except ValueError:
        raise APIError('Page must be a positive integer.')
    if page < 1 or page > 100000:
        raise APIError('Page must be a positive integer.')
    return page


def _text(value, label, limit, required=True):
    value = str(value or '').strip()
    if (required and not value) or len(value) > limit:
        raise APIError(f'{label} is required and must be at most {limit} characters.')
    return value


def _case_json(case, include_messages=True):
    db = get_db()
    organization = db.get(Organization, case.organization_id)
    opener = db.get(User, case.opened_by)
    result = {'id': case.id, 'organization_id': case.organization_id,
        'organization_name': organization.name if organization else 'Organization',
        'subject': case.subject, 'category': case.category, 'description': case.description,
        'status': case.status, 'opened_by_name': opener.name if opener else 'Organization staff',
        'opened_by_email': opener.email if opener else None,
        'created_at': iso(case.created_at), 'updated_at': iso(case.updated_at)}
    if include_messages:
        messages = db.scalars(select(SupportMessage).where(
            SupportMessage.case_id == case.id,
            SupportMessage.organization_id == case.organization_id,
        ).order_by(SupportMessage.created_at, SupportMessage.id)).all()
        authors = {user.id: user for user in db.scalars(select(User).where(
            User.id.in_({message.author_id for message in messages}))).all()} if messages else {}
        result['messages'] = [{'id': item.id, 'author_type': item.author_type,
            'author_name': authors[item.author_id].name if item.author_id in authors else 'Support',
            'message': item.message, 'created_at': iso(item.created_at)} for item in messages]
    return result


@bp.route('/support-cases', methods=['GET', 'POST'])
@require_org('read')
def organization_cases():
    db = get_db()
    if request.method == 'GET':
        page = _page()
        query = select(SupportCase).where(SupportCase.organization_id == g.organization.id)
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        cases = db.scalars(query.order_by(SupportCase.updated_at.desc(), SupportCase.id)
            .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)).all()
        return {'rows': [_case_json(case) for case in cases], 'total': total,
            'page': page, 'page_size': PAGE_SIZE}
    data = body()
    category = _text(data.get('category'), 'Category', 40)
    if category not in CATEGORIES:
        raise APIError('Choose a listed support category.')
    case = SupportCase(organization_id=g.organization.id, opened_by=g.user.id,
        subject=_text(data.get('subject'), 'Subject', 180), category=category,
        description=_text(data.get('description'), 'Description', 12000))
    db.add(case)
    db.flush()
    audit('support.case_opened', 'support_case', case.id,
        after={'category': case.category, 'status': case.status},
        organization_id=g.organization.id)
    db.commit()
    return {'case': _case_json(case)}, 201


@bp.post('/support-cases/<case_id>/messages')
@require_org('read')
def organization_message(case_id):
    db = get_db()
    case = db.scalar(select(SupportCase).where(
        SupportCase.id == valid_id(case_id),
        SupportCase.organization_id == g.organization.id).with_for_update())
    if not case:
        raise APIError('Support case not found in this organization.', 404, 'not_found')
    if case.status in {'resolved', 'closed'}:
        raise APIError('Reopen the case with a new ticket to continue this conversation.', 409, 'case_closed')
    item = SupportMessage(organization_id=case.organization_id, case_id=case.id,
        author_id=g.user.id, author_type='organization',
        message=_text(body().get('message'), 'Message', 12000))
    case.status = 'open'
    case.updated_at = now()
    db.add(item)
    audit('support.message_added', 'support_case', case.id,
        after={'author_type': 'organization'}, organization_id=case.organization_id)
    db.commit()
    return {'case': _case_json(case)}


@bp.get('/platform/support-cases')
@require_platform_admin
def platform_cases():
    page = _page()
    status = request.args.get('status', '').strip()
    if status and status not in STATUSES:
        raise APIError('Choose a valid support case status.')
    query = select(SupportCase)
    if status:
        query = query.where(SupportCase.status == status)
    total = get_db().scalar(select(func.count()).select_from(query.subquery())) or 0
    cases = get_db().scalars(query.order_by(SupportCase.updated_at.desc(),
        SupportCase.id).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)).all()
    rows = [_case_json(case) for case in cases]
    for case in cases:
        set_scope(case.organization_id)
        audit('support.case_viewed', 'support_case', case.id,
            after={'status': case.status}, reason='Platform administrator viewed support correspondence',
            organization_id=case.organization_id)
        # Audit rows must be persisted before switching the tenant context.
        get_db().flush()
    if cases:
        get_db().commit()
    return {'rows': rows, 'total': total, 'page': page, 'page_size': PAGE_SIZE}


@bp.post('/platform/support-cases/<case_id>/messages')
@require_platform_admin
def platform_message(case_id):
    db = get_db()
    case = db.scalar(select(SupportCase).where(SupportCase.id == valid_id(case_id)).with_for_update())
    if not case:
        raise APIError('Support case not found.', 404, 'not_found')
    item = SupportMessage(organization_id=case.organization_id, case_id=case.id,
        author_id=g.user.id, author_type='platform_admin',
        message=_text(body().get('message'), 'Message', 12000))
    if case.status in {'open', 'resolved', 'closed'}:
        case.status = 'in_progress'
    case.updated_at = now()
    db.add(item)
    set_scope(case.organization_id)
    audit('support.message_added', 'support_case', case.id,
        after={'author_type': 'platform_admin'}, organization_id=case.organization_id)
    db.commit()
    return {'case': _case_json(case)}


@bp.patch('/platform/support-cases/<case_id>')
@require_platform_admin
def platform_update_case(case_id):
    db = get_db()
    case = db.scalar(select(SupportCase).where(SupportCase.id == valid_id(case_id)).with_for_update())
    if not case:
        raise APIError('Support case not found.', 404, 'not_found')
    status = _text(body().get('status'), 'Status', 30)
    if status not in STATUSES:
        raise APIError('Choose a valid support case status.')
    before = {'status': case.status}
    case.status = status
    case.updated_at = now()
    set_scope(case.organization_id)
    audit('support.case_status_changed', 'support_case', case.id,
        before=before, after={'status': status}, organization_id=case.organization_id)
    db.commit()
    return {'case': _case_json(case)}
