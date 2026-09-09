import os,io,time,json,hmac,hashlib,tempfile,unittest
from unittest.mock import patch,Mock
import test_api
import server,services
from database import engine,metadata,users,profiles,photos,action_tokens,subscriptions,guide_stats
from sqlalchemy import select,insert,update
from PIL import Image
class ProductTests(unittest.TestCase):
    register=test_api.ApiTests.register
    def setUp(self):
        test_api.ApiTests.setUp(self)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.env=patch.dict(os.environ,{'UPLOAD_DIR':self.temp.name+'/uploads','ERASURE_DIR':self.temp.name+'/erasures','BILLING_REQUIRED':'0','STRIPE_SECRET_KEY':'sk_test_mock','STRIPE_PRICE_ID':'price_mock','STRIPE_WEBHOOK_SECRET':'whsec_mock'})
        self.env.start();self.addCleanup(self.env.stop)
        self.register(self.client)
    def uid(self):
        with self.client.session_transaction() as s:return s['uid']
    def property(self,**d):return self.client.post('/api/properties',json={'name':'Test',**d}).json
    def image(self):
        buf=io.BytesIO();im=Image.new('RGB',(100,80),'red');exif=im.getexif();exif[0x010E]='private metadata';im.save(buf,'JPEG',exif=exif);buf.seek(0);return buf
    def test_upload_private_public_and_metadata(self):
        r=self.client.post('/api/photos',data={'photo':(self.image(),'test.jpg')},content_type='multipart/form-data')
        self.assertEqual(r.status_code,201);url=r.json['url']
        guest=server.app.test_client();self.assertEqual(guest.get(url).status_code,404)
        with self.client.get(url) as own:
            photo=Image.open(io.BytesIO(own.data));self.assertEqual(photo.format,'WEBP');self.assertFalse(photo.getexif())
        p=self.property(image=url,images=[url],published=True)
        with guest.get(url) as public:self.assertEqual(public.status_code,200)
        self.client.put('/api/properties/'+p['code'],json={**p,'published':False})
        self.assertEqual(guest.get(url).status_code,404)
        other=server.app.test_client();self.register(other,'other@example.com')
        self.assertEqual(other.post('/api/properties',json={'name':'Steal','image':url}).status_code,400)
        self.assertEqual(self.client.post('/api/photos',data={'photo':(io.BytesIO(b'<script>'), 'fake.jpg')}).status_code,400)
    def test_upload_csrf_rejected(self):
        self.assertEqual(self.client.post('/api/photos',data={'photo':(self.image(),'x.jpg')},headers={'Origin':'https://evil.example'}).status_code,403)
    def test_password_reset_is_single_use_and_revokes_sessions(self):
        other=server.app.test_client();other.post('/api/auth/login',json={'email':'host@example.com','password':'test-password-123'})
        token='secret-once'
        with engine.begin() as c:c.execute(insert(action_tokens).values(digest=hashlib.sha256(token.encode()).hexdigest(),owner=self.uid(),purpose='reset',expires=int(time.time())+300))
        response=self.client.post('/api/auth/consume-token',json={'token':token,'purpose':'reset','password':'new-password-123'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(other.get('/api/me').status_code,401)
        self.assertEqual(self.client.post('/api/auth/consume-token',json={'token':token,'purpose':'reset','password':'bad-password-123'}).status_code,400)
        self.assertEqual(self.client.post('/api/auth/login',json={'email':'host@example.com','password':'new-password-123'}).status_code,200)
    def test_email_verification_and_expired_token(self):
        with engine.begin() as c:
            for raw,purpose,expiry in [('verify','verify',time.time()+300),('expired','reset',time.time()-1)]:
                c.execute(insert(action_tokens).values(digest=hashlib.sha256(raw.encode()).hexdigest(),owner=self.uid(),purpose=purpose,expires=int(expiry)))
        self.assertEqual(self.client.post('/api/auth/consume-token',json={'token':'expired','purpose':'reset','password':'new-password-123'}).status_code,400)
        self.assertEqual(self.client.post('/api/auth/consume-token',json={'token':'verify','purpose':'verify'}).status_code,200)
        self.assertTrue(self.client.get('/api/account').json['verified'])
    def test_export_rights_and_erasure(self):
        p=self.property(published=True)
        self.client.post('/api/account/privacy-requests',json={'kind':'correction','message':'Please correct my data'})
        exported=self.client.get('/api/account/export').json
        self.assertNotIn('password',exported['account']);self.assertEqual(len(exported['privacyRequests']),1)
        self.assertEqual(self.client.delete('/api/account',json={'confirm':'host@example.com','password':'wrong'}).status_code,401)
        self.assertEqual(self.client.delete('/api/account',json={'confirm':'host@example.com','password':'test-password-123'}).status_code,200)
        self.assertEqual(self.client.get('/api/guides/'+p['code']).status_code,404)
        self.assertEqual(self.client.get('/api/me').status_code,401)
        self.assertTrue(os.path.isfile(self.temp.name+'/erasures/erasure-log.jsonl'))
    def test_admin_requires_verified_email(self):
        with patch.dict(os.environ,{'ADMIN_EMAILS':'host@example.com'}):
            self.assertEqual(self.client.get('/api/admin/privacy-requests').status_code,403)
            with engine.begin() as c:c.execute(update(profiles).where(profiles.c.owner==self.uid()).values(verified=True))
            self.assertEqual(self.client.get('/api/admin/privacy-requests').status_code,200)
    def test_billing_enforcement_and_signed_idempotent_webhook(self):
        p=self.property(published=False);uid=self.uid()
        with engine.begin() as c:
            c.execute(insert(subscriptions).values(code=p['code'],owner=uid,status='none',paid_until=0,cancel_at_period_end=False,checkout_expires=0))
        event={'id':'evt_test','type':'customer.subscription.updated','data':{'object':{'id':'sub_test'}}}
        raw=json.dumps(event).encode();stamp=int(time.time());signature=hmac.new(b'whsec_mock',str(stamp).encode()+b'.'+raw,hashlib.sha256).hexdigest()
        current={'id':'sub_test','metadata':{'property_code':p['code'],'owner':str(uid)},'status':'active','customer':'cus_test','current_period_end':stamp+86400,'items':{'data':[{'price':{'id':'price_mock'}}]}}
        self.assertEqual(self.client.post('/api/billing/webhook',data=raw,content_type='application/json').status_code,400)
        with patch.dict(os.environ,{'BILLING_REQUIRED':'1'}):
            self.assertEqual(self.client.put('/api/properties/'+p['code'],json={**p,'published':True}).status_code,402)
            with patch('product.stripe_call',return_value=current) as provider:
                for _ in range(2):
                    response=self.client.post('/api/billing/webhook',data=raw,content_type='application/json',headers={'Stripe-Signature':f't={stamp},v1={signature}'})
                    self.assertEqual(response.status_code,200)
                self.assertEqual(provider.call_count,2)
            self.assertEqual(self.client.put('/api/properties/'+p['code'],json={**p,'published':True}).status_code,200)
            with engine.begin() as c:c.execute(update(subscriptions).where(subscriptions.c.code==p['code']).values(status='canceled'))
            self.assertEqual(self.client.get('/api/guides/'+p['code']).status_code,404)
    def test_checkout_requires_verified_email_and_fixed_price(self):
        p=self.property()
        self.assertEqual(self.client.post('/api/billing/'+p['code']+'/checkout',json={}).status_code,403)
        with engine.begin() as c:c.execute(update(profiles).where(profiles.c.owner==self.uid()).values(verified=True))
        with patch('product.stripe_call',side_effect=[{'currency':'eur','unit_amount':1900,'recurring':{'interval':'year'}},{'id':'cs_mock','url':'https://checkout.stripe.com/test','expires_at':int(time.time())+3600}]) as call:
            first=self.client.post('/api/billing/'+p['code']+'/checkout',json={})
            second=self.client.post('/api/billing/'+p['code']+'/checkout',json={})
            self.assertEqual(first.status_code,200);self.assertEqual(second.json,first.json);self.assertEqual(call.call_count,2)
    def test_places_and_statistics(self):
        a=Mock();a.json.return_value={'features':[{'geometry':{'coordinates':[2.3,48.8]},'properties':{'formatted':'Paris'}}]}
        b=Mock();b.json.return_value={'features':[{'properties':{'name':'Café','formatted':'Rue Test'}}]}
        with patch.dict(os.environ,{'GEOAPIFY_API_KEY':'mock'}),patch('product.requests.get',side_effect=[a,b]):
            self.assertEqual(self.client.post('/api/places',json={'location':'Paris','lang':'fr'}).json['places'][0]['name'],'Café')
        p=self.property(published=True)
        guest=server.app.test_client();guest.get('/api/guides/'+p['code']);guest.get('/api/guides/'+p['code'])
        self.assertEqual(self.client.get('/api/properties/'+p['code']+'/stats').json['total'],2)
    def test_terms_and_legal_identity(self):
        other=server.app.test_client()
        self.assertEqual(other.post('/api/auth/register',json={'name':'X','email':'new@example.com','password':'test-password-123'}).json['error'],'terms_required')
        legal=self.client.get('/api/legal').json
        self.assertEqual(legal['entity'],'Audela de donnees');self.assertTrue(legal['draft'])
        self.assertEqual(self.client.get('/api/account').json['termsVersion'],server.LEGAL_VERSION)
    def test_backup_encryption_authenticates(self):
        from scripts.backup import encrypt
        from scripts.restore import decrypt
        from cryptography.exceptions import InvalidTag
        from pathlib import Path
        import base64
        root=Path(self.temp.name);(root/'in').write_bytes(os.urandom(2000000));key=base64.urlsafe_b64encode(os.urandom(32)).decode()
        encrypt(root/'in',root/'encrypted',key);decrypt(root/'encrypted',root/'out',key)
        self.assertEqual((root/'in').read_bytes(),(root/'out').read_bytes())
        blob=bytearray((root/'encrypted').read_bytes());blob[50]^=1;(root/'encrypted').write_bytes(blob)
        with self.assertRaises(InvalidTag):decrypt(root/'encrypted',root/'out',key)
if __name__=='__main__':unittest.main()
