"""Volleyball Manager application factory."""
import os,secrets,uuid,logging,json,time
from pathlib import Path
from flask import Flask,g,request,jsonify
from dotenv import load_dotenv
from sqlalchemy.exc import IntegrityError,OperationalError,DataError
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import SecurityError
from .db import init_db,get_db
from .common import APIError

ROOT=Path(__file__).resolve().parents[1]

def create_app(config=None):
    load_dotenv(ROOT/'.env')
    app=Flask(__name__,instance_path=str(ROOT/'instance'),instance_relative_config=True)
    app.config.update(
        SECRET_KEY=os.environ.get('SECRET_KEY'),DATABASE_URL=os.environ.get('DATABASE_URL'),
        APP_ENV=os.environ.get('APP_ENV','development'),PUBLIC_BASE_URL=os.environ.get('PUBLIC_BASE_URL','http://127.0.0.1:5050'),
        SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE','true').lower()=='true',
        PERMANENT_SESSION_LIFETIME=28800,MAX_CONTENT_LENGTH=8*1024*1024,
        PRIVATE_STORAGE_PATH=os.environ.get('PRIVATE_STORAGE_PATH',str(ROOT/'instance'/'files')),BACKUP_STATUS_PATH=os.environ.get('BACKUP_STATUS_PATH'),SCHEMA_REVISION=os.environ.get('SCHEMA_REVISION'),
        TRUST_PROXY_HEADERS=os.environ.get('TRUST_PROXY_HEADERS','false').lower()=='true',
        TRUSTED_HOSTS=[host.strip() for host in os.environ.get('TRUSTED_HOSTS','').split(',') if host.strip()] or None,
        MFA_ENCRYPTION_KEY=os.environ.get('MFA_ENCRYPTION_KEY'),
        WEATHER_PROVIDER_URL=os.environ.get('WEATHER_PROVIDER_URL'),WEATHER_PROVIDER_NAME=os.environ.get('WEATHER_PROVIDER_NAME'),
        WEATHER_LICENSE_CONFIRMED=os.environ.get('WEATHER_LICENSE_CONFIRMED','false').lower()=='true',WEATHER_PROVIDER_TOKEN=os.environ.get('WEATHER_PROVIDER_TOKEN'),
        WEATHER_PROVIDER_TYPE=os.environ.get('WEATHER_PROVIDER_TYPE','normalized'),WEATHER_PROVIDER_API_KEY=os.environ.get('WEATHER_PROVIDER_API_KEY'),
        GEOCODING_PROVIDER=os.environ.get('GEOCODING_PROVIDER'),GEOCODING_PROVIDER_URL=os.environ.get('GEOCODING_PROVIDER_URL'),
        GEOCODING_LICENSE_CONFIRMED=os.environ.get('GEOCODING_LICENSE_CONFIRMED','false').lower()=='true',
        SMTP_HOST=os.environ.get('SMTP_HOST'),SMTP_PORT=int(os.environ.get('SMTP_PORT','587')),
        SMTP_USER=os.environ.get('SMTP_USER'),SMTP_PASSWORD=os.environ.get('SMTP_PASSWORD'),SMTP_FROM=os.environ.get('SMTP_FROM'),
        EMAIL_DELIVERY_MODE=os.environ.get('EMAIL_DELIVERY_MODE','deferred'),
        ALLOW_DEMO_SEED=False,
    )
    if config:app.config.update(config)
    if not app.config['SECRET_KEY']:raise RuntimeError('Set a strong SECRET_KEY before starting the application.')
    if app.config['APP_ENV']=='production':
        if len(app.config['SECRET_KEY'])<48 or not app.config['MFA_ENCRYPTION_KEY'] or not app.config['SESSION_COOKIE_SECURE']:
            raise RuntimeError('Production requires strong SECRET_KEY, separate MFA_ENCRYPTION_KEY and secure cookies.')
    if app.config['TRUST_PROXY_HEADERS']:
        # Enable only when the app is reachable through the single trusted proxy.
        app.wsgi_app=ProxyFix(app.wsgi_app,x_for=1,x_proto=1,x_host=0,x_port=0,x_prefix=0)
    Path(app.instance_path).mkdir(exist_ok=True)
    init_db(app)
    @app.before_request
    def identify_request():
        g.request_id=str(uuid.uuid4());g.request_started=time.perf_counter()
    from . import auth,admin,competition,scoring,reports,weather,operations,ui,live,locations
    auth.init_auth(app)
    @app.before_request
    def rate_limit_api():
        from flask import session
        path=request.path
        if path.startswith('/api/v1/auth/') or not path.startswith('/api/v1/'):return
        from .auth import _throttle
        if path.startswith('/api/v1/referee/'):
            key=session.get('referee_token') or request.remote_addr or 'local'
            _throttle('scorer_limit',key,600 if path.endswith('/action') else 120,1)
        elif path.startswith('/api/v1/public/'):
            if 'visitor_rate_id' not in session:session['visitor_rate_id']=secrets.token_urlsafe(18)
            _throttle('public_limit',session['visitor_rate_id'],180,1)
        elif path in ('/api/v1/exports','/api/v1/reports/export','/api/v1/imports/preview') and request.method in ('POST','GET'):
            _throttle('report_limit',session.get('auth_session_id') or request.remote_addr or 'local',30,1)
    for module in (auth,admin,competition,scoring,reports,weather,operations,ui,live,locations):app.register_blueprint(module.bp)
    reports.init_reports(app)
    from .cli import init_cli
    init_cli(app)
    @app.errorhandler(APIError)
    def api_error(err):
        get_db().rollback()
        return jsonify(error={'code':err.code,'message':err.message,'details':err.details},request_id=getattr(g,'request_id',None)),err.status
    @app.errorhandler(IntegrityError)
    def integrity_error(err):
        get_db().rollback()
        return jsonify(error={'code':'integrity_conflict','message':'This change conflicts with an existing record or relationship. Refresh and review the current data.','details':None},request_id=getattr(g,'request_id',None)),409
    @app.errorhandler(DataError)
    def data_error(err):
        get_db().rollback();return jsonify(error={'code':'validation','message':'A field has an invalid value. Review the request.','details':None}),400
    @app.errorhandler(OperationalError)
    def database_error(err):
        get_db().rollback()
        return jsonify(error={'code':'database_retry','message':'The database is temporarily unavailable or busy. Pending actions remain unsaved; retry safely.','details':None},request_id=getattr(g,'request_id',None)),503
    @app.errorhandler(404)
    def missing(err):return jsonify(error={'code':'not_found','message':'Page or endpoint not found.','details':None}),404
    @app.errorhandler(SecurityError)
    def untrusted_host(err):return jsonify(error={'code':'invalid_host','message':'The requested host is not allowed.','details':None}),400
    @app.errorhandler(413)
    def too_large(err):return jsonify(error={'code':'file_size','message':'Upload exceeds the 8 MB limit.','details':None}),413
    @app.errorhandler(Exception)
    def unexpected(err):
        if app.testing:raise err
        app.logger.error('request_failed request_id=%s type=%s path=%s',getattr(g,'request_id',None),type(err).__name__,request.path)
        get_db().rollback();return jsonify(error={'code':'internal','message':'The request could not complete. Use the request ID when contacting support.','details':None},request_id=getattr(g,'request_id',None)),500
    @app.after_request
    def secure_response(response):
        response.headers['X-Request-ID']=getattr(g,'request_id','')
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=(self), screen-wake-lock=(self)'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self'; media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.path.startswith('/api/') or request.path=='/referee':response.headers['Cache-Control']='no-store, private'
        if app.config['SESSION_COOKIE_SECURE']:response.headers['Strict-Transport-Security']='max-age=31536000'
        if not app.testing and request.path.startswith('/api/'):
            app.logger.info(json.dumps({'event':'api_response','request_id':g.request_id,
                'route':str(request.url_rule) if request.url_rule else 'unmatched','method':request.method,
                'status':response.status_code,'duration_ms':round((time.perf_counter()-g.request_started)*1000,2),
                'organization_id':g.db.info.get('organization_id') if 'db' in g else None,
                'match_id':getattr(getattr(g,'scorer',None),'match_id',None)}))
        return response
    return app
