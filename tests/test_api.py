import os, tempfile, unittest
os.environ['TESTING']='1'
os.environ['DATABASE_URL']=os.environ.get('TEST_DATABASE_URL') or 'sqlite:///'+tempfile.mktemp(suffix='.sqlite3')
import server
from sqlalchemy import delete
server.init_db()
class ApiTests(unittest.TestCase):
    def setUp(self):
        server.app.config['TESTING']=True
        server.app.logger.setLevel('WARNING')
        self.client=server.app.test_client()
        from database import metadata
        with server.engine.begin() as c:
            for table in reversed(metadata.sorted_tables): c.execute(delete(table))
    def register(self,client,email='host@example.com'):
        return client.post('/api/auth/register',json={'name':'Host','email':email,'password':'test-password-123','acceptTerms':True})
    def test_account_and_ownership(self):
        self.assertEqual(self.client.get('/api/properties').status_code,401)
        self.assertEqual(self.register(self.client).status_code,200)
        p=self.client.post('/api/properties',json={'name':'Test House','content':{'pt':{'rules':'Silêncio'}}}).json
        self.assertEqual(len(self.client.get('/api/properties').json),1)
        other=server.app.test_client();self.register(other,'other@example.com')
        self.assertEqual(other.put('/api/properties/'+p['code'],json={'name':'Stolen'}).status_code,404)
        self.assertEqual(other.delete('/api/properties/'+p['code'],json={}).status_code,404)
    def test_publication_lifecycle(self):
        self.register(self.client)
        p=self.client.post('/api/properties',json={'name':'Casa','published':False}).json
        url='/api/guides/'+p['code']
        self.assertEqual(self.client.get(url).status_code,404)
        self.client.put('/api/properties/'+p['code'],json={**p,'published':True})
        public=server.app.test_client()
        self.assertEqual(public.get(url).json['name'],'Casa')
        self.client.put('/api/properties/'+p['code'],json={**p,'published':False})
        self.assertEqual(public.get(url).status_code,404)
        self.client.delete('/api/properties/'+p['code'],json={})
        self.assertEqual(self.client.get('/api/properties').json,[])
    def test_auth_validation(self):
        self.assertEqual(self.client.post('/api/auth/register',json={'name':'Host','email':'invalid','password':'short'}).status_code,400)
        self.register(self.client)
        self.client.post('/api/logout',json={})
        self.assertEqual(self.client.post('/api/auth/login',json={'email':'host@example.com','password':'wrong'}).status_code,401)
        self.assertEqual(self.client.post('/api/auth/login',json={'email':'host@example.com','password':'test-password-123','acceptTerms':True}).status_code,200)
        self.assertEqual(self.client.post('/api/properties',json={'name':' '}).status_code,400)
    def test_public_page_route(self):
        response=self.client.get("/AB123")
        self.assertEqual(response.status_code,200)
        response.close()

    def test_cross_origin_rejected(self):
        self.assertEqual(self.client.post('/api/auth/register',json={},headers={'Origin':'https://other.example'}).status_code,403)
if __name__=='__main__':unittest.main()
