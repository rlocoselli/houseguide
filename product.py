import os, time, json, secrets, hashlib, hmac, io, warnings
from datetime import datetime, timezone
from flask import request, session, jsonify, send_file, g
from sqlalchemy import select, insert, update, delete, func
from sqlalchemy.exc import IntegrityError
from PIL import Image, ImageOps, UnidentifiedImageError
import requests
from database import engine, users, properties, profiles, action_tokens, photos, subscriptions, webhook_events, guide_stats, privacy_requests
from services import ServiceError, profile, rate_limit, issue_mail, mail_configured, stripe_configured, stripe_call, entitled, cancel_billing, upload_dir, upsert

LEGAL_VERSION='2026-09-08-v1'

def setup_product(app,auth):
    @app.errorhandler(ServiceError)
    def service_error(e): return jsonify(error=e.code),e.status

    @app.get('/health/live')
    def live(): return jsonify(status='ok')

    @app.get('/health/ready')
    def ready():
        try:
            with engine.connect() as c: c.execute(select(users.c.id).limit(1))
            return jsonify(status='ok')
        except Exception: return jsonify(status='unavailable'),503

    @app.get('/api/legal')
    def legal():
        fields={'entity':os.getenv('LEGAL_ENTITY_NAME','Audela de donnees'),'address':os.getenv('LEGAL_ADDRESS',''),'registration':os.getenv('LEGAL_REGISTRATION',''),'country':os.getenv('LEGAL_COUNTRY','France'),'privacy':os.getenv('PRIVACY_EMAIL','admin@audeladedonnees.fr'),'support':os.getenv('SUPPORT_EMAIL','admin@audeladedonnees.fr'),'version':LEGAL_VERSION}
        return jsonify(**fields,draft=not all(fields.values()))

    @app.get('/api/account')
    @auth
    def account():
        with engine.begin() as c:
            p=profile(c,session['uid'])
            u=c.execute(select(users.c.name,users.c.email,users.c.google_sub,users.c.password).where(users.c.id==session['uid'])).mappings().one()
        return jsonify(name=u['name'],email=u['email'],googleLinked=bool(u['google_sub']),hasPassword=bool(u['password']),verified=p['verified'],termsVersion=p['terms_version'],mailEnabled=mail_configured())

    @app.put('/api/account')
    @auth
    def correct_account():
        d=request.get_json();name=d.get('name') if isinstance(d,dict) else None
        if not isinstance(name,str) or not name.strip() or len(name)>100: raise ServiceError('validation')
        with engine.begin() as c: c.execute(update(users).where(users.c.id==session['uid']).values(name=name.strip()))
        return jsonify(ok=True)

    @app.post('/api/account/accept-terms')
    @auth
    def accept_terms():
        if request.get_json().get('acceptTerms') is not True: raise ServiceError('terms_required')
        with engine.begin() as c:
            profile(c,session['uid']);c.execute(update(profiles).where(profiles.c.owner==session['uid']).values(terms_version=LEGAL_VERSION,accepted_at=int(time.time())))
        return jsonify(ok=True)

    @app.post('/api/account/verify-email')
    @auth
    def send_verification():
        rate_limit('verify:'+str(session['uid']),3)
        with engine.connect() as c: email=c.execute(select(users.c.email).where(users.c.id==session['uid'])).scalar_one()
        issue_mail(session['uid'],email,'verify');return jsonify(ok=True)

    @app.post('/api/auth/forgot-password')
    def forgot():
        d=request.get_json();email=str(d.get('email','')).strip().lower()
        rate_limit('reset:'+email,3)
        if not mail_configured(): raise ServiceError('mail_not_configured',503)
        with engine.connect() as c: u=c.execute(select(users).where(users.c.email==email)).mappings().first()
        if u and u['password']:
            try: issue_mail(u['id'],u['email'],'reset')
            except ServiceError: pass # Do not disclose whether an email is registered.
        return jsonify(ok=True)

    @app.post('/api/auth/consume-token')
    def consume_token():
        from werkzeug.security import generate_password_hash
        d=request.get_json();token=d.get('token','');purpose=d.get('purpose')
        if not isinstance(token,str) or len(token)>200 or purpose not in ('verify','reset'): raise ServiceError('validation')
        password=d.get('password','')
        if purpose=='reset' and (not isinstance(password,str) or not 10<=len(password)<=1024): raise ServiceError('validation')
        with engine.begin() as c:
            row=c.execute(select(action_tokens).where(action_tokens.c.digest==hashlib.sha256(token.encode()).hexdigest()).with_for_update()).mappings().first()
            if not row or row['purpose']!=purpose or row['expires']<time.time(): raise ServiceError('invalid_token')
            profile(c,row['owner'])
            if purpose=='reset':
                c.execute(update(users).where(users.c.id==row['owner']).values(password=generate_password_hash(password)))
                c.execute(update(profiles).where(profiles.c.owner==row['owner']).values(session_version=profiles.c.session_version+1))
            else: c.execute(update(profiles).where(profiles.c.owner==row['owner']).values(verified=True))
            c.execute(delete(action_tokens).where((action_tokens.c.owner==row['owner'])&(action_tokens.c.purpose==purpose)))
        if purpose=='reset': session.clear()
        return jsonify(ok=True)

    @app.get('/api/account/export')
    @auth
    def export_account():
        uid=session['uid'];rate_limit('export:'+str(uid),5)
        with engine.begin() as c:
            u=dict(c.execute(select(users.c.name,users.c.email).where(users.c.id==uid)).mappings().one())
            payload={'account':u,'preferences':dict(profile(c,uid)),
                'properties':[dict(r['data'],code=r['code']) for r in c.execute(select(properties).where(properties.c.owner==uid)).mappings()],
                'subscriptions':[dict(r) for r in c.execute(select(subscriptions).where(subscriptions.c.owner==uid)).mappings()],
                'privacyRequests':[dict(r) for r in c.execute(select(privacy_requests).where(privacy_requests.c.owner==uid)).mappings()],
                'photos':[{'url':'/media/'+r['id'],'bytes':r['bytes']} for r in c.execute(select(photos).where(photos.c.owner==uid)).mappings()]}
        payload['preferences'].pop('session_version',None)
        response=jsonify(payload);response.headers['Content-Disposition']='attachment; filename="houseguide-data.json"';return response

    @app.route('/api/account/privacy-requests',methods=['GET','POST'])
    @auth
    def rights_request():
        uid=session['uid']
        with engine.begin() as c:
            if request.method=='GET': return jsonify([dict(r) for r in c.execute(select(privacy_requests).where(privacy_requests.c.owner==uid)).mappings()])
            rate_limit('rights:'+str(uid),5)
            d=request.get_json();kind=d.get('kind');message=d.get('message','')
            if kind not in ('access','correction','restriction','objection','portability','erasure','other') or not isinstance(message,str) or not 1<=len(message)<=4000: raise ServiceError('validation')
            code=secrets.token_hex(8);c.execute(insert(privacy_requests).values(id=code,owner=uid,kind=kind,message=message,created=int(time.time()),status='received'))
        return jsonify(id=code,status='received'),201

    @app.delete('/api/account')
    @auth
    def erase_account():
        from werkzeug.security import check_password_hash
        from google.oauth2 import id_token
        from google.auth.transport.requests import Request
        uid=session['uid'];d=request.get_json()
        with engine.begin() as c:
            u=c.execute(select(users).where(users.c.id==uid).with_for_update()).mappings().one()
            if d.get('confirm')!=u['email']: raise ServiceError('validation')
            verified=bool(u['password'] and isinstance(d.get('password'),str) and check_password_hash(u['password'],d['password']))
            if not verified and d.get('credential') and u['google_sub']:
                try:
                    claims=id_token.verify_oauth2_token(d['credential'],Request(),os.environ['GOOGLE_CLIENT_ID'])
                    verified=claims.get('sub')==u['google_sub'] and claims.get('aud')==os.environ['GOOGLE_CLIENT_ID']
                except Exception: verified=False
            if not verified: raise ServiceError('credentials',401)
            for code in c.execute(select(properties.c.code).where(properties.c.owner==uid)).scalars(): cancel_billing(c,code)
            ids=list(c.execute(select(photos.c.id).where(photos.c.owner==uid)).scalars())
            # Explicit deletions also work in isolated SQLite tests.
            codes=select(properties.c.code).where(properties.c.owner==uid)
            c.execute(delete(guide_stats).where(guide_stats.c.code.in_(codes)))
            for table in (subscriptions,privacy_requests,action_tokens,profiles,photos): c.execute(delete(table).where(table.c.owner==uid))
            from database import ai_usage
            c.execute(delete(ai_usage).where(ai_usage.c.owner==uid));c.execute(delete(properties).where(properties.c.owner==uid));c.execute(delete(users).where(users.c.id==uid))
            from database import erasures
            c.execute(insert(erasures).values(owner=uid,created=int(time.time())))
        for photo_id in ids: (upload_dir()/(photo_id+'.webp')).unlink(missing_ok=True)
        from services import write_erasure_ledger
        try: write_erasure_ledger()
        except OSError: app.logger.error('erasure_ledger_sync_failed')
        session.clear();return jsonify(ok=True)

    @app.post('/api/photos')
    @auth
    def upload_photo():
        rate_limit('upload:'+str(session['uid']),30)
        file=request.files.get('photo')
        if not file: raise ServiceError('invalid_photo')
        Image.MAX_IMAGE_PIXELS=24_000_000
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error',Image.DecompressionBombWarning)
                image=Image.open(file.stream)
                if image.format not in ('JPEG','PNG','WEBP'): raise ServiceError('invalid_photo')
                image.load();image=ImageOps.exif_transpose(image).convert('RGB');image.thumbnail((1920,1920))
                clean=Image.new('RGB',image.size);clean.paste(image);buffer=io.BytesIO();clean.save(buffer,format='WEBP',quality=85)
        except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError,Image.DecompressionBombWarning): raise ServiceError('invalid_photo') from None
        photo_id=secrets.token_hex(16);blob=buffer.getvalue();path=upload_dir()/(photo_id+'.webp')
        try:
            with engine.begin() as c:
                c.execute(select(users.c.id).where(users.c.id==session['uid']).with_for_update()).one()
                if c.execute(select(func.count()).select_from(photos).where(photos.c.owner==session['uid'])).scalar_one()>=100: raise ServiceError('photo_limit')
                path.write_bytes(blob);path.chmod(0o600)
                c.execute(insert(photos).values(id=photo_id,owner=session['uid'],created=int(time.time()),bytes=len(blob)))
        except Exception:
            path.unlink(missing_ok=True);raise
        return jsonify(url='/media/'+photo_id),201

    @app.get('/media/<photo_id>')
    def media(photo_id):
        if len(photo_id)!=32 or any(x not in '0123456789abcdef' for x in photo_id): raise ServiceError('not_found',404)
        with engine.connect() as c:
            row=c.execute(select(photos).where(photos.c.id==photo_id)).mappings().first()
            if not row: raise ServiceError('not_found',404)
            allowed=session.get('uid')==row['owner']
            if not allowed:
                url='/media/'+photo_id
                allowed=any(r['data'].get('published') and (r['data'].get('image')==url or url in r['data'].get('images',[])) and entitled(c,r['code']) for r in c.execute(select(properties).where(properties.c.owner==row['owner'])).mappings())
            if not allowed: raise ServiceError('not_found',404)
        path=upload_dir()/(photo_id+'.webp')
        if not path.exists(): raise ServiceError('not_found',404)
        response=send_file(path,mimetype='image/webp');response.headers['Cache-Control']='private, no-store';return response

    @app.get('/api/properties/<code>/stats')
    @auth
    def stats(code):
        with engine.connect() as c:
            if not c.execute(select(properties.c.code).where((properties.c.code==code)&(properties.c.owner==session['uid']))).first(): raise ServiceError('not_found',404)
            rows=list(c.execute(select(guide_stats.c.day,guide_stats.c.views).where(guide_stats.c.code==code).order_by(guide_stats.c.day)).mappings())
        return jsonify(total=sum(r['views'] for r in rows),days=[dict(r) for r in rows])

    @app.post('/api/places')
    @auth
    def places():
        rate_limit('places:'+str(session['uid']),20)
        key=os.getenv('GEOAPIFY_API_KEY')
        if not key: raise ServiceError('places_not_configured',503)
        d=request.get_json();location=d.get('location');lang=d.get('lang','pt');category=d.get('category','restaurants')
        if not isinstance(location,str) or not 3<=len(location)<=300 or lang not in ('pt','it','en','de','fr','es'): raise ServiceError('validation')
        categories={'restaurants':'catering.restaurant','supermarkets':'commercial.supermarket','shops':'commercial.shop','services':'service'}
        if category not in categories: raise ServiceError('validation')
        try:
            geo=requests.get('https://api.geoapify.com/v1/geocode/search',params={'text':location,'limit':1,'lang':lang,'apiKey':key},timeout=15);geo.raise_for_status()
            features=geo.json().get('features',[])
            if not features: return jsonify(places=[],attribution='Geoapify / OpenStreetMap')
            lon,lat=features[0]['geometry']['coordinates']
            r=requests.get('https://api.geoapify.com/v2/places',params={'categories':categories[category],'filter':f'circle:{lon},{lat},1500','bias':f'proximity:{lon},{lat}','limit':10,'lang':lang,'apiKey':key},timeout=15);r.raise_for_status()
            items=[{'name':f['properties'].get('name','Restaurant'),'address':f['properties'].get('formatted','')} for f in r.json()['features']]
            return jsonify(places=items,attribution='Geoapify / OpenStreetMap',location=features[0]['properties'].get('formatted',location))
        except (requests.RequestException,ValueError,KeyError,TypeError): raise ServiceError('places_unavailable',502) from None

    @app.get('/api/billing')
    @auth
    def billing_status():
        with engine.connect() as c:
            rows=[dict(r) for r in c.execute(select(subscriptions).where(subscriptions.c.owner==session['uid'])).mappings()]
        return jsonify(enabled=stripe_configured(),required=os.getenv('BILLING_REQUIRED')=='1',subscriptions=[{k:r[k] for k in ('code','status','paid_until','cancel_at_period_end')} for r in rows])

    @app.post('/api/billing/<code>/checkout')
    @auth
    def checkout(code):
        rate_limit('checkout:'+str(session['uid']),10)
        if not stripe_configured(): raise ServiceError('billing_not_configured',503)
        origin=os.getenv('PUBLIC_ORIGIN','http://localhost:5000').rstrip('/')
        with engine.begin() as c:
            p=c.execute(select(properties).where((properties.c.code==code)&(properties.c.owner==session['uid'])).with_for_update()).mappings().first()
            if not p: raise ServiceError('not_found',404)
            if not profile(c,session['uid'])['verified']: raise ServiceError('verify_required',403)
            c.execute(upsert(subscriptions).values(code=code,owner=session['uid'],status='none',paid_until=0,cancel_at_period_end=False,checkout_expires=0).on_conflict_do_nothing(index_elements=['code']))
            sub=c.execute(select(subscriptions).where(subscriptions.c.code==code)).mappings().one()
            if sub['status'] in ('active','trialing','past_due','unpaid'): raise ServiceError('subscription_exists',409)
            if sub['checkout_url'] and sub['checkout_expires']>time.time(): return jsonify(url=sub['checkout_url'])
            email=c.execute(select(users.c.email).where(users.c.id==session['uid'])).scalar_one()
            price=stripe_call('GET','prices/'+os.environ['STRIPE_PRICE_ID'])
            if price.get('currency')!='eur' or price.get('unit_amount')!=1900 or price.get('recurring',{}).get('interval')!='year' or price.get('recurring',{}).get('interval_count',1)!=1: raise ServiceError('billing_price_invalid',503)
            window=int(time.time())//1800
            result=stripe_call('POST','checkout/sessions',{
                'mode':'subscription','line_items[0][price]':os.environ['STRIPE_PRICE_ID'],'line_items[0][quantity]':'1',
                'customer_email':email,'client_reference_id':code,'subscription_data[metadata][property_code]':code,
                'subscription_data[metadata][owner]':str(session['uid']),
                'success_url':origin+'/?billing=success','cancel_url':origin+'/?billing=cancelled',
                'expires_at':str((window+2)*1800)},idempotency='checkout-'+code+'-'+str(window))
            c.execute(update(subscriptions).where(subscriptions.c.code==code).values(checkout_id=result['id'],checkout_url=result['url'],checkout_expires=result['expires_at']))
        return jsonify(url=result['url'])

    @app.post('/api/billing/<code>/portal')
    @auth
    def portal(code):
        with engine.connect() as c: row=c.execute(select(subscriptions).where((subscriptions.c.code==code)&(subscriptions.c.owner==session['uid']))).mappings().first()
        if not row or not row['customer_id']: raise ServiceError('not_found',404)
        r=stripe_call('POST','billing_portal/sessions',{'customer':row['customer_id'],'return_url':os.getenv('PUBLIC_ORIGIN','http://localhost:5000').rstrip('/')+'/'})
        return jsonify(url=r['url'])

    @app.post('/api/billing/webhook')
    def webhook():
        key=os.getenv('STRIPE_WEBHOOK_SECRET')
        if not key: raise ServiceError('billing_not_configured',503)
        raw=request.get_data();parts=request.headers.get('Stripe-Signature','').split(',')
        values={}
        for part in parts:
            if '=' in part:
                k,v=part.split('=',1);values.setdefault(k,[]).append(v)
        try:
            timestamp=int(values['t'][0]);expected=hmac.new(key.encode(),str(timestamp).encode()+b'.'+raw,hashlib.sha256).hexdigest()
            if abs(time.time()-timestamp)>300 or not any(hmac.compare_digest(expected,x) for x in values.get('v1',[])): raise ValueError()
            event=json.loads(raw)
        except (ValueError,KeyError,TypeError): raise ServiceError('invalid_signature',400)
        event_type=event.get('type','');obj=event.get('data',{}).get('object',{})
        if event_type not in ('customer.subscription.created','customer.subscription.updated','customer.subscription.deleted','checkout.session.completed'): return jsonify(ok=True)
        sid=obj.get('subscription') if event_type=='checkout.session.completed' else obj.get('id')
        if not isinstance(sid,str) or not sid.startswith('sub_'): raise ServiceError('validation')
        try:
            with engine.begin() as c:
                if c.execute(select(webhook_events.c.id).where(webhook_events.c.id==event['id'])).first(): return jsonify(ok=True)
                # Read current Stripe state under the property lock, not stale event payloads.
                current=stripe_call('GET','subscriptions/'+sid);code=current.get('metadata',{}).get('property_code')
                row=c.execute(select(subscriptions).where(subscriptions.c.code==code).with_for_update()).mappings().first()
                if not row: return jsonify(ok=True)
                if str(row['owner'])!=current.get('metadata',{}).get('owner'): raise ServiceError('validation')
                if row['subscription_id'] and row['subscription_id']!=sid and row['status'] not in ('canceled','incomplete_expired','none'): raise ServiceError('subscription_exists',409)
                current=stripe_call('GET','subscriptions/'+sid)
                items=current.get('items',{}).get('data',[])
                until=current.get('current_period_end') or max((i.get('current_period_end',0) for i in items),default=0)
                price_valid=any(i.get('price',{}).get('id')==os.getenv('STRIPE_PRICE_ID') for i in items)
                c.execute(update(subscriptions).where(subscriptions.c.code==code).values(subscription_id=sid,customer_id=current['customer'],status=current['status'] if price_valid else 'invalid_price',paid_until=until,cancel_at_period_end=current.get('cancel_at_period_end',False),checkout_expires=0))
                c.execute(insert(webhook_events).values(id=event['id'],created=int(time.time())))
        except IntegrityError: return jsonify(ok=True)
        return jsonify(ok=True)

    @app.get('/api/admin/privacy-requests')
    @auth
    def admin_rights():
        with engine.connect() as c:
            email=c.execute(select(users.c.email).where(users.c.id==session['uid'])).scalar_one()
            if email not in os.getenv('ADMIN_EMAILS','').split(',') or not c.execute(select(profiles.c.verified).where(profiles.c.owner==session['uid'])).scalar_one_or_none(): raise ServiceError('forbidden',403)
            rows=c.execute(select(privacy_requests,users.c.email).join(users,privacy_requests.c.owner==users.c.id).order_by(privacy_requests.c.created)).mappings()
            return jsonify([dict(r) for r in rows])

    @app.put('/api/admin/privacy-requests/<code>')
    @auth
    def admin_resolve(code):
        with engine.begin() as c:
            email=c.execute(select(users.c.email).where(users.c.id==session['uid'])).scalar_one()
            if email not in os.getenv('ADMIN_EMAILS','').split(',') or not c.execute(select(profiles.c.verified).where(profiles.c.owner==session['uid'])).scalar_one_or_none(): raise ServiceError('forbidden',403)
            status=request.get_json().get('status')
            if status not in ('received','in_progress','completed'): raise ServiceError('validation')
            c.execute(update(privacy_requests).where(privacy_requests.c.id==code).values(status=status))
        return jsonify(ok=True)
