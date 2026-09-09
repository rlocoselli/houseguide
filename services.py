"""Server-side services used by the monolith's product routes."""
import os, time, secrets, hashlib, hmac, smtplib, ssl, json
from pathlib import Path
from email.message import EmailMessage
from sqlalchemy import select, insert, update, delete
import requests
from database import engine, profiles, limits, action_tokens, subscriptions

class ServiceError(Exception):
    def __init__(self, code, status=400): self.code, self.status = code, status

def upsert(table):
    if engine.dialect.name == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert as statement
    else:
        from sqlalchemy.dialects.sqlite import insert as statement
    return statement(table)

def profile(c, uid):
    c.execute(upsert(profiles).values(owner=uid, verified=False, session_version=0, terms_version='', accepted_at=0).on_conflict_do_nothing(index_elements=['owner']))
    return c.execute(select(profiles).where(profiles.c.owner == uid)).mappings().one()

def rate_limit(identity, maximum=30, seconds=3600):
    window=int(time.time())//seconds
    key=hmac.new(os.environ.get('SECRET_KEY','local-limits').encode(),identity.encode(),hashlib.sha256).hexdigest()
    with engine.begin() as c:
        stmt=upsert(limits).values(key=key,window=window,count=1).on_conflict_do_update(index_elements=['key','window'],set_={'count':limits.c.count+1},where=limits.c.count<maximum).returning(limits.c.count)
        if c.execute(stmt).scalar_one_or_none() is None: raise ServiceError('rate_limited',429)

def mail_configured(): return bool(os.getenv('SMTP_HOST') and os.getenv('MAIL_FROM'))
def issue_mail(uid, email, purpose):
    if not mail_configured(): raise ServiceError('mail_not_configured',503)
    raw=secrets.token_urlsafe(32); digest=hashlib.sha256(raw.encode()).hexdigest()
    expires=int(time.time())+(3600 if purpose=='reset' else 86400)
    with engine.begin() as c:
        c.execute(delete(action_tokens).where((action_tokens.c.owner==uid)&(action_tokens.c.purpose==purpose)))
        c.execute(insert(action_tokens).values(digest=digest,owner=uid,purpose=purpose,expires=expires))
    origin=os.getenv('PUBLIC_ORIGIN','http://localhost:5000').rstrip('/')
    link=f'{origin}/account-action#{purpose}={raw}'
    message=EmailMessage();message['From']=os.environ['MAIL_FROM'];message['To']=email
    message['Subject']='House Guide — '+('Password reset / Redefinir senha' if purpose=='reset' else 'Verify email / Verificar e-mail')
    message.set_content(f'House Guide\n\n{link}\n\nThis one-time link expires in '+('1 hour.' if purpose=='reset' else '24 hours.')+'\nEste link é de uso único. Se você não solicitou, ignore esta mensagem.\n\n'+os.getenv('SUPPORT_EMAIL',''))
    try:
        factory=smtplib.SMTP_SSL if os.getenv('SMTP_SSL')=='1' else smtplib.SMTP
        with factory(os.environ['SMTP_HOST'],int(os.getenv('SMTP_PORT','587')),timeout=15) as smtp:
            if os.getenv('SMTP_SSL')!='1' and os.getenv('SMTP_STARTTLS','1')=='1': smtp.starttls(context=ssl.create_default_context())
            if os.getenv('SMTP_USER'): smtp.login(os.environ['SMTP_USER'],os.environ.get('SMTP_PASSWORD',''))
            smtp.send_message(message)
    except (OSError,smtplib.SMTPException):
        with engine.begin() as c: c.execute(delete(action_tokens).where(action_tokens.c.digest==digest))
        raise ServiceError('mail_unavailable',503) from None

def stripe_configured(): return bool(os.getenv('STRIPE_SECRET_KEY') and os.getenv('STRIPE_PRICE_ID') and os.getenv('STRIPE_WEBHOOK_SECRET'))
def stripe_call(method,path,data=None,idempotency=None):
    if not stripe_configured(): raise ServiceError('billing_not_configured',503)
    headers={'Authorization':'Bearer '+os.environ['STRIPE_SECRET_KEY']}
    if idempotency: headers['Idempotency-Key']=idempotency
    try:
        r=requests.request(method,'https://api.stripe.com/v1/'+path,headers=headers,data=data,timeout=(10,30))
        if r.status_code>=400: raise ServiceError('billing_unavailable',502)
        return r.json()
    except (requests.RequestException,ValueError): raise ServiceError('billing_unavailable',502) from None

def entitled(c,code):
    if os.getenv('BILLING_REQUIRED','0')!='1': return True
    row=c.execute(select(subscriptions).where(subscriptions.c.code==code)).mappings().first()
    return bool(row and row['status'] in ('active','trialing') and row['paid_until']>time.time())

def cancel_billing(c,code):
    row=c.execute(select(subscriptions).where(subscriptions.c.code==code).with_for_update()).mappings().first()
    if not row: return
    # Expire open checkout as well: it must not recreate a charge after deletion.
    if row['checkout_id'] and row['checkout_expires']>time.time():
        checkout=stripe_call('GET','checkout/sessions/'+row['checkout_id'])
        if checkout['status']=='open': stripe_call('POST','checkout/sessions/'+row['checkout_id']+'/expire')
        if checkout.get('subscription'):
            stripe_call('DELETE','subscriptions/'+checkout['subscription'])
    if row['subscription_id'] and row['status'] not in ('canceled','incomplete_expired'):
        stripe_call('DELETE','subscriptions/'+row['subscription_id'])
    c.execute(update(subscriptions).where(subscriptions.c.code==code).values(status='canceled',paid_until=0,checkout_expires=0))

def upload_dir():
    p=Path(os.getenv('UPLOAD_DIR',str(Path(__file__).parent/'.local/uploads')));p.mkdir(parents=True,exist_ok=True);return p

def write_erasure_ledger():
    from database import erasures
    from sqlalchemy import text
    root=Path(os.getenv('ERASURE_DIR',str(Path(__file__).parent/'.local/erasures')));root.mkdir(parents=True,exist_ok=True);root.chmod(0o700)
    with engine.connect() as c:
        if engine.dialect.name=='postgresql':c.execute(text('SELECT pg_advisory_lock(741904)'))
        try:
            rows=[dict(r) for r in c.execute(select(erasures)).mappings()]
            tmp=root/('ledger-'+secrets.token_hex(8)+'.tmp')
            with open(tmp,'x') as f:
                os.chmod(tmp,0o600)
                for row in rows:f.write(json.dumps(row)+'\n')
                f.flush();os.fsync(f.fileno())
            os.replace(tmp,root/'erasure-log.jsonl')
            if os.getenv('BACKUP_S3_BUCKET'):
                import boto3
                client=boto3.client('s3',endpoint_url=os.getenv('S3_ENDPOINT_URL') or None)
                client.upload_file(str(root/'erasure-log.jsonl'),os.environ['BACKUP_S3_BUCKET'],'houseguide/erasure-log.jsonl',ExtraArgs={'ServerSideEncryption':'AES256'})

        finally:
            if engine.dialect.name=='postgresql':c.execute(text('SELECT pg_advisory_unlock(741904)'))
