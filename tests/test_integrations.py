import json
import os
import unittest
from unittest.mock import patch, Mock
import requests
import test_api
import server
import ai_service
from sqlalchemy import select

class Integrations(unittest.TestCase):
    register = test_api.ApiTests.register
    def setUp(self):
        test_api.ApiTests.setUp(self)
        self.env = patch.dict(os.environ, {'KIMI_API_KEY':'test-secret-never-exposed','KIMI_MODEL':'test-model','GOOGLE_CLIENT_ID':'test-client','AI_REQUESTS_PER_HOUR':'20'})
        self.env.start(); self.addCleanup(self.env.stop)

    def test_config_exposes_no_secrets(self):
        self.assertEqual(self.client.get('/api/config').json, {'googleClientId':'test-client','aiEnabled':True,'termsVersion':server.LEGAL_VERSION})
        self.assertNotIn('test-secret', self.client.get('/api/config').text)

    def test_ai_requires_auth_and_valid_input(self):
        self.assertEqual(self.client.post('/api/ai/rules',json={'lang':'pt'}).status_code,401)
        self.register(self.client)
        with patch('ai_service.completion') as completion:
            for payload in ({'source':{},'targets':['en']},{'source':{'password':'secret'},'targets':['en']},{'source':{'rules':'Hi'},'targets':[['en']]},{'source':{'rules':'Hi'},'targets':['xx']},{'source':{'rules':'Hi'},'targets':['en','en']}):
                self.assertEqual(self.client.post('/api/ai/translate',json=payload).status_code,400)
            self.assertEqual(self.client.post('/api/ai/rules',json={'lang':[]}).status_code,400)
            completion.assert_not_called()

    def test_translation_proposal_does_not_write_property(self):
        self.register(self.client)
        p=self.client.post('/api/properties',json={'name':'Casa','content':{'pt':{'rules':'Não fumar'}}}).json
        result={'translations':{'en':{'rules':'No smoking'},'fr':{'rules':'Ne pas fumer'}}}
        with patch('ai_service.completion',return_value=result):
            response=self.client.post('/api/ai/translate',json={'source':p['content']['pt'],'targets':['en','fr']})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json,result)
        self.assertEqual(self.client.get('/api/properties').json[0]['content'],p['content'])

    def test_provider_payload_and_rules(self):
        self.register(self.client)
        upstream=Mock(status_code=200)
        upstream.json.return_value={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'rules':'Cuide da casa.'})}}]}
        with patch('ai_service.requests.post',return_value=upstream) as post:
            response=self.client.post('/api/ai/rules',json={'lang':'pt','context':'Animais permitidos','existing':''})
        self.assertEqual(response.json,{'rules':'Cuide da casa.'})
        self.assertEqual(post.call_args.args[0],'https://api.moonshot.ai/v1/chat/completions')
        self.assertEqual(post.call_args.kwargs['headers']['Authorization'],'Bearer test-secret-never-exposed')
        self.assertEqual(post.call_args.kwargs['json']['model'],'test-model')
        self.assertIn('Animais permitidos',post.call_args.kwargs['json']['messages'][1]['content'])

    def test_provider_errors_are_safe(self):
        self.register(self.client)
        for code in (401,500,429):
            with patch('ai_service.requests.post',return_value=Mock(status_code=code)):
                r=self.client.post('/api/ai/rules',json={'lang':'pt'})
                self.assertEqual(r.status_code,429 if code==429 else 502)
                self.assertNotIn('test-secret',r.text)
        with patch('ai_service.requests.post',side_effect=requests.Timeout):
            self.assertEqual(self.client.post('/api/ai/rules',json={'lang':'pt'}).status_code,504)
        with patch.dict(os.environ,{'KIMI_API_KEY':''}):
            self.assertEqual(self.client.post('/api/ai/rules',json={'lang':'pt'}).status_code,503)

    def test_malformed_translation_rejected(self):
        self.register(self.client)
        with patch('ai_service.completion',return_value={'translations':{'en':{'extra':'invented'}}}):
            self.assertEqual(self.client.post('/api/ai/translate',json={'source':{'rules':'Texto'},'targets':['en']}).status_code,502)
        with patch('ai_service.completion',return_value={'rules':42}):
            self.assertEqual(self.client.post('/api/ai/rules',json={'lang':'pt'}).status_code,502)

    def test_ai_limit_shared_in_database(self):
        self.register(self.client)
        with patch.dict(os.environ,{'AI_REQUESTS_PER_HOUR':'1'}), patch('ai_service.suggest_rules',return_value='Rule') as suggest:
            self.assertEqual(self.client.post('/api/ai/rules',json={'lang':'pt'}).status_code,200)
            self.assertEqual(self.client.post('/api/ai/rules',json={'lang':'pt'}).status_code,429)
            self.assertEqual(suggest.call_count,1)

    def test_sqlite_migration_preserves_data_and_passwords(self):
        import tempfile, sqlite3
        from scripts.migrate_sqlite import migrate
        with tempfile.TemporaryDirectory() as folder:
            path=folder+'/legacy.sqlite3'
            with sqlite3.connect(path) as c:
                c.executescript('CREATE TABLE users(id INTEGER PRIMARY KEY,email TEXT,password TEXT,name TEXT); CREATE TABLE properties(code TEXT,owner INTEGER,data TEXT);')
                c.execute('INSERT INTO users VALUES(?,?,?,?)',(42,'legacy@example.com',server.generate_password_hash('legacy-password'),'Legacy'))
                c.execute('INSERT INTO properties VALUES(?,?,?)',('KEEP123',42,json.dumps({'name':'Old House','published':True,'content':{'pt':{'rules':'Regras'}}})))
            migrate(path)
            self.assertEqual(self.client.get('/api/guides/KEEP123').json['name'],'Old House')
            self.assertEqual(self.client.post('/api/auth/login',json={'email':'legacy@example.com','password':'legacy-password'}).status_code,200)
            self.assertEqual(self.client.get('/api/properties').json[0]['code'],'KEEP123')
            with self.assertRaises(RuntimeError): migrate(path)
            with sqlite3.connect(path) as c:
                self.assertEqual(c.execute('SELECT id FROM users').fetchone()[0],42)

    def claims(self,**changes):
        return dict({'sub':'google-123','email':'google@example.com','email_verified':True,'name':'Google Host','iss':'https://accounts.google.com','aud':'test-client'},**changes)

    def test_google_login_create_and_reuse(self):
        with patch('server.id_token.verify_oauth2_token',return_value=self.claims()) as verify:
            self.assertEqual(self.client.post('/api/auth/google',json={'credential':'signed-token','acceptTerms':True}).status_code,200)
            self.assertEqual(verify.call_args.args[2],'test-client')
            self.assertEqual(self.client.get('/api/me').json['email'],'google@example.com')
            self.client.post('/api/logout',json={})
            self.assertEqual(self.client.post('/api/auth/google',json={'credential':'signed-token','acceptTerms':True}).status_code,200)
        with server.engine.connect() as c:
            self.assertEqual(len(c.execute(select(server.users)).all()),1)
        self.assertEqual(self.client.post('/api/auth/login',json={'email':'google@example.com','password':'anything'}).status_code,401)

    def test_google_invalid_claims_and_token(self):
        for claims in [self.claims(email_verified=False),self.claims(aud='other'),self.claims(iss='https://evil.example')]:
            with patch('server.id_token.verify_oauth2_token',return_value=claims):
                self.assertEqual(self.client.post('/api/auth/google',json={'credential':'bad-token'}).status_code,401)
        with patch('server.id_token.verify_oauth2_token',side_effect=ValueError('expired')):
            self.assertEqual(self.client.post('/api/auth/google',json={'credential':'expired'}).status_code,401)
        self.assertEqual(self.client.get('/api/me').status_code,401)

    def test_google_does_not_take_over_password_account(self):
        self.register(self.client,'google@example.com')
        self.client.post('/api/logout',json={})
        with patch('server.id_token.verify_oauth2_token',return_value=self.claims()):
            self.assertEqual(self.client.post('/api/auth/google',json={'credential':'valid','acceptTerms':True}).status_code,409)
        self.assertEqual(self.client.get('/api/me').status_code,401)

if __name__=='__main__':unittest.main()
