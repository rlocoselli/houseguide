import os
import re
import secrets
import time
from pathlib import Path
from functools import wraps
from urllib.parse import urlparse

from dotenv import load_dotenv
from flask import Flask, request, session, jsonify, send_from_directory, g
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import select, insert, update, delete
from sqlalchemy.exc import IntegrityError
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from google.auth.exceptions import GoogleAuthError

ROOT = Path(__file__).parent
load_dotenv(ROOT / '.env')
from database import engine, users, properties as property_table, ai_usage, init_db
import ai_service
from services import profile, ServiceError, rate_limit, entitled, cancel_billing, upsert
from product import setup_product, LEGAL_VERSION
from database import profiles, photos, guide_stats
from datetime import datetime, timezone

app = Flask(__name__, static_folder=None)
import logging
app.logger.setLevel(logging.INFO)
if os.getenv('TRUST_PROXY_HOPS')=='1':
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app=ProxyFix(app.wsgi_app,x_for=1,x_proto=1,x_host=1)
secret = ROOT / '.secret'
if not os.getenv('SECRET_KEY') and not secret.exists():
    fd = os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(secrets.token_hex(32))
app.secret_key = os.getenv('SECRET_KEY') or secret.read_text()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                  SESSION_COOKIE_SECURE=os.getenv('HTTPS') == '1', MAX_CONTENT_LENGTH=256*1024)

def auth(fn):
    @wraps(fn)
    def wrapped(*a, **kw):
        if not session.get('uid'): return jsonify(error='unauthorized'),401
        with engine.begin() as c:
            if not c.execute(select(users.c.id).where(users.c.id==session['uid'])).first():
                session.clear();return jsonify(error='unauthorized'),401
            current=profile(c,session['uid'])
            if session.get('version',0)!=current['session_version']:
                session.clear();return jsonify(error='unauthorized'),401
        return fn(*a, **kw)
    return wrapped

@app.before_request
def origin_check():
    g.started=time.monotonic();g.request_id=secrets.token_hex(8)
    if request.method in ('POST','PUT','DELETE') and engine.dialect.name=='postgresql':
        from sqlalchemy import text
        g.backup_lock=engine.connect();g.backup_lock.execute(text('SELECT pg_advisory_lock_shared(741903)'))
    if request.path=='/api/billing/webhook': return
    if request.path=='/api/photos': request.max_content_length=8*1024*1024
    if request.path.startswith('/api/auth/') and request.method=='POST':
        rate_limit('auth-ip:'+str(request.remote_addr),60)
    if request.method in ('POST', 'PUT', 'DELETE'):
        if not request.is_json and request.path!='/api/photos':
            return jsonify(error='json_required'), 415
        origin = request.headers.get('Origin')
        expected = os.getenv('PUBLIC_ORIGIN', request.host_url).rstrip('/')
        if origin and origin.rstrip('/') != expected:
            if not (app.debug and urlparse(origin).hostname in ('localhost', '127.0.0.1')):
                return jsonify(error='origin'), 403
        if request.is_json and not isinstance(request.get_json(silent=True),dict): return jsonify(error='validation'),400

@app.teardown_request
def unlock_backup(error):
    lock=g.pop('backup_lock',None)
    if lock is not None:
        from sqlalchemy import text
        try: lock.execute(text('SELECT pg_advisory_unlock_shared(741903)'))
        finally: lock.close()

@app.after_request
def private_responses(response):
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['X-Frame-Options']='SAMEORIGIN'
    response.headers['X-Request-ID']=getattr(g,'request_id','')
    response.headers['Permissions-Policy']='geolocation=(), microphone=(), camera=()'
    if os.getenv('HTTPS')=='1': response.headers['Strict-Transport-Security']='max-age=31536000'
    app.logger.info('request endpoint=%s status=%s request_id=%s duration_ms=%s',request.endpoint,response.status_code,getattr(g,'request_id',''),int((time.monotonic()-getattr(g,'started',time.monotonic()))*1000))
    return response

@app.get('/api/config')
def public_config():
    return jsonify(googleClientId=os.getenv('GOOGLE_CLIENT_ID', ''), aiEnabled=ai_service.configured(),termsVersion=LEGAL_VERSION)

def signed_in(u,c):
    session.clear()
    session['uid'] = u['id']
    session['version']=profile(c,u['id'])['session_version']
    session['authenticated_at']=int(time.time())
    return jsonify(name=u['name'], email=u['email'])

@app.post('/api/auth/<action>')
def login(action):
    d = request.get_json()
    if not isinstance(d, dict) or action not in ('login', 'register'):
        return jsonify(error='validation'), 400
    email = str(d.get('email', '')).strip().lower()
    password = str(d.get('password', ''))
    if len(email) > 320 or len(password) > 1024:
        return jsonify(error='validation'), 400
    try:
        with engine.begin() as c:
            if action == 'register':
                if d.get('acceptTerms') is not True: raise ServiceError('terms_required')
                name = str(d.get('name', '')).strip()[:100]
                if not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email) or len(password) < 10 or not name:
                    return jsonify(error='validation'), 400
                c.execute(insert(users).values(email=email, password=generate_password_hash(password), name=name))
            u = c.execute(select(users).where(users.c.email == email)).mappings().first()
            if not u or not u['password'] or not check_password_hash(u['password'], password):
                return jsonify(error='credentials'), 401
            if action=='register':
                profile(c,u['id']);c.execute(update(profiles).where(profiles.c.owner==u['id']).values(terms_version=LEGAL_VERSION,accepted_at=int(time.time())))
            return signed_in(u,c)
    except IntegrityError:
        return jsonify(error='exists'), 409

@app.post('/api/auth/google')
def google_login(link=False):
    client_id = os.getenv('GOOGLE_CLIENT_ID')
    if not client_id:
        return jsonify(error='google_not_configured'), 503
    d = request.get_json()
    if not isinstance(d, dict) or not isinstance(d.get('credential'), str) or len(d['credential']) > 16000:
        return jsonify(error='validation'), 400
    try:
        claims = id_token.verify_oauth2_token(d['credential'], google_requests.Request(), client_id)
        if (claims.get('iss') not in ('accounts.google.com', 'https://accounts.google.com')
            or claims.get('aud') != client_id or claims.get('email_verified') is not True
            or not claims.get('sub') or not claims.get('email')):
            return jsonify(error='credentials'), 401
        email = claims['email'].strip().lower()
        with engine.begin() as c:
            if link:
                if time.time()-session.get('authenticated_at',0)>300: raise ServiceError('reauth_required',403)
                u=c.execute(select(users).where(users.c.id==session['uid'])).mappings().one()
                c.execute(update(users).where(users.c.id==u['id']).values(google_sub=claims['sub']))
                return jsonify(name=u['name'],email=u['email'])
            u = c.execute(select(users).where(users.c.google_sub == claims['sub'])).mappings().first()
            if not u:
                if d.get('acceptTerms') is not True: raise ServiceError('terms_required')
                # Never silently link an existing password account by email.
                if c.execute(select(users.c.id).where(users.c.email == email)).first():
                    return jsonify(error='google_account_exists'), 409
                uid = c.execute(insert(users).values(email=email, password=None,
                    name=str(claims.get('name') or email.split('@')[0])[:100], google_sub=claims['sub']).returning(users.c.id)).scalar_one()
                u = {'id': uid, 'email': email, 'name': str(claims.get('name') or email.split('@')[0])[:100]}
                profile(c,uid);c.execute(update(profiles).where(profiles.c.owner==uid).values(verified=True,terms_version=LEGAL_VERSION,accepted_at=int(time.time())))
            return signed_in(u,c)
    except (ValueError, GoogleAuthError):
        return jsonify(error='credentials'), 401
    except IntegrityError:
        return jsonify(error='google_account_exists'), 409

@app.post('/api/account/link-google')
@auth
def link_google(): return google_login(link=True)

@app.post('/api/logout')
def logout():
    session.clear()
    return jsonify(ok=True)

@app.get('/api/me')
@auth
def me():
    with engine.connect() as c:
        u = c.execute(select(users.c.name, users.c.email).where(users.c.id == session['uid'])).mappings().first()
    if not u:
        session.clear()
        return jsonify(error='unauthorized'), 401
    return jsonify(dict(u))

def validated(d):
    if not isinstance(d, dict): return None
    defaults = {'name': '', 'location': '', 'image': '', 'published': False, 'content': {}, 'wifi': '', 'password': '', 'checkout': '11:00', 'images': []}
    result = {k: d.get(k, v) for k, v in defaults.items()}
    if any(not isinstance(result[k], str) or len(result[k]) > 2000 for k in ('name', 'location', 'image', 'wifi', 'password', 'checkout')): return None
    if not result['name'].strip() or not isinstance(result['published'], bool) or not isinstance(result['content'], dict): return None
    if not isinstance(result['images'],list) or len(result['images'])>12: return None
    for url in [result['image']]+result['images']:
        if not isinstance(url,str) or len(url)>2000: return None
        if not url: continue
        if url.startswith('/media/'):
            with engine.connect() as c:
                if not c.execute(select(photos.c.id).where((photos.c.id==url[7:])&(photos.c.owner==session.get('uid')))).first(): return None
        elif not url.startswith(('https://','http://')): return None
    for lang, content in result['content'].items():
        if lang not in ai_service.LANGUAGES or not isinstance(content, dict): return None
        if any(k not in ai_service.FIELDS or not isinstance(v, str) or len(v) > 10000 for k, v in content.items()): return None
    return result

@app.route('/api/properties', methods=['GET', 'POST'])
@auth
def properties():
    with engine.begin() as c:
        if request.method=='POST': c.execute(select(users.c.id).where(users.c.id==session['uid']).with_for_update()).first()
        if request.method == 'GET':
            rows = c.execute(select(property_table).where(property_table.c.owner == session['uid']).order_by(property_table.c.code)).mappings()
            return jsonify([dict(r['data'], code=r['code']) for r in rows])
        d = validated(request.get_json())
        if d is None: return jsonify(error='validation'), 400
        if d['published'] and os.getenv('BILLING_REQUIRED')=='1': raise ServiceError('subscription_required',402)
        code = secrets.token_hex(6).upper()
        c.execute(insert(property_table).values(code=code, owner=session['uid'], data=d))
        return jsonify(dict(d, code=code)), 201

@app.route('/api/properties/<code>', methods=['PUT', 'DELETE'])
@auth
def property_edit(code):
    condition = (property_table.c.code == code) & (property_table.c.owner == session['uid'])
    with engine.begin() as c:
        c.execute(select(users.c.id).where(users.c.id==session['uid']).with_for_update()).first()
        if not c.execute(select(property_table.c.code).where(condition)).first(): return jsonify(error='not_found'), 404
        if request.method == 'DELETE':
            cancel_billing(c,code)
            c.execute(delete(guide_stats).where(guide_stats.c.code==code))
            from database import subscriptions
            c.execute(delete(subscriptions).where(subscriptions.c.code==code))
            c.execute(delete(property_table).where(condition))
            return jsonify(ok=True)
        d = validated(request.get_json())
        if d is None: return jsonify(error='validation'), 400
        if d['published'] and not entitled(c,code): raise ServiceError('subscription_required',402)
        c.execute(update(property_table).where(condition).values(data=d))
        return jsonify(dict(d, code=code))

@app.get('/api/guides/<code>')
def guide(code):
    with engine.begin() as c:
        d = c.execute(select(property_table.c.data).where(property_table.c.code == code)).scalar_one_or_none()
        if not d or not d.get('published') or not entitled(c,code): return jsonify(error='not_found'),404
        day=datetime.now(timezone.utc).date().isoformat()
        c.execute(upsert(guide_stats).values(code=code,day=day,views=1).on_conflict_do_update(index_elements=['code','day'],set_={'views':guide_stats.c.views+1}))
    return jsonify(dict(d, code=code))

def consume_ai_quota():
    uid, window = session['uid'], int(time.time()) // 3600
    limit = int(os.getenv('AI_REQUESTS_PER_HOUR', '20'))
    if engine.dialect.name == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert as upsert
    else:
        from sqlalchemy.dialects.sqlite import insert as upsert
    from sqlalchemy import case
    with engine.begin() as c:
        statement = upsert(ai_usage).values(owner=uid, window=window, count=1)
        statement = statement.on_conflict_do_update(index_elements=['owner'], set_={
            'window': window, 'count': case((ai_usage.c.window == window, ai_usage.c.count + 1), else_=1)
        }, where=(ai_usage.c.window != window) | (ai_usage.c.count < limit)).returning(ai_usage.c.count)
        if statement is not None and c.execute(statement).scalar_one_or_none() is None:
            raise ai_service.AIError('ai_rate_limit', 429)

@app.post('/api/ai/<action>')
@auth
def ai_action(action):
    d = request.get_json()
    if not isinstance(d, dict) or action not in ('translate', 'rules'): return jsonify(error='validation'), 400
    if action == 'translate':
        source, targets = d.get('source'), d.get('targets')
        if (not isinstance(source, dict) or not source or set(source) - ai_service.FIELDS
            or any(not isinstance(v, str) or not v.strip() or len(v) > 10000 for v in source.values())
            or not isinstance(targets, list) or not 1 <= len(targets) <= 5
            or any(not isinstance(l, str) or l not in ai_service.LANGUAGES for l in targets)
            or len(set(targets)) != len(targets)):
            return jsonify(error='validation'), 400
        if sum(len(v) for v in source.values()) > 20000: return jsonify(error='validation'), 400
    else:
        lang, context, existing = d.get('lang'), d.get('context', ''), d.get('existing', '')
        if (not isinstance(lang, str) or lang not in ai_service.LANGUAGES
            or not isinstance(context, str) or len(context) > 4000
            or not isinstance(existing, str) or len(existing) > 10000):
            return jsonify(error='validation'), 400
    try:
        if not ai_service.configured(): raise ai_service.AIError('ai_not_configured', 503)
        consume_ai_quota()
        if action == 'translate': return jsonify(translations=ai_service.translate(source, targets))
        return jsonify(rules=ai_service.suggest_rules(lang, context, existing))
    except ai_service.AIError as e:
        return jsonify(error=e.code), e.status

@app.cli.command('init-db')
def init_db_command():
    init_db()
    print('Database schema initialized.')

@app.get('/')
@app.get('/<path:path>')
def index(path=''):
    if path.startswith('api/'): return jsonify(error='not_found'), 404
    if path and (ROOT / 'dist' / path).is_file(): return send_from_directory(ROOT / 'dist', path)
    return send_from_directory(ROOT / 'dist', 'index.html')

setup_product(app,auth)

@app.errorhandler(413)
def too_large(e): return jsonify(error='file_too_large'),413

@app.errorhandler(500)
def unexpected(e): return jsonify(error='server_error',requestId=getattr(g,'request_id','')),500

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '5000')), debug=os.getenv('FLASK_DEBUG') == '1')
