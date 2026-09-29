import sys,tempfile,unittest,json,http.client,threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import analytics,server
HEADER=','.join(analytics.COLUMNS)+'\n'
DATA=HEADER+'A,2026-01-01,Keyboard,Electronics,Nador,2,100.00,60.00\nA,2026-01-01,Mouse,Electronics,Nador,1,50.00,20.00\nB,2026-01-02,Jacket,Clothing,Oujda,1,200.00,150.00\nC,2025-12-30,Keyboard,Electronics,Nador,1,100.00,60.00\n'
class AnalyticsTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'test.db';analytics.initialize(self.path);self.db=analytics.connect(self.path);self.batch=analytics.import_csv(self.db,DATA,'test','MAD')['id']
 def tearDown(self):self.db.close();self.temp.cleanup()
 def test_exact_kpis(self):
  d=analytics.dashboard(self.db,self.batch,{})['kpis'];self.assertEqual(d['revenue_cents'],55000);self.assertEqual(d['profit_cents'],20000);self.assertEqual(d['orders'],3);self.assertEqual(d['units'],5);self.assertEqual(d['margin'],36.36)
 def test_filter_and_previous(self):
  d=analytics.dashboard(self.db,self.batch,{'start':'2026-01-01','end':'2026-01-02'});self.assertEqual(d['kpis']['revenue_cents'],45000);self.assertEqual(d['previous']['revenue_cents'],10000);self.assertEqual(d['previous']['revenue_change_percent'],350)
  d=analytics.dashboard(self.db,self.batch,{'region':'Nador','category':'Electronics'});self.assertEqual(d['kpis']['orders'],2)
 def test_duplicate_and_invalid_rows(self):
  line='X,2026-01-01,P,C,R,1,10,5\n';r=analytics.validate(HEADER+line+line+'Y,2026-02-30,P,C,R,1,10,5\n');self.assertEqual(r['accepted'],1);self.assertEqual(r['rejected'],2)
 def test_order_dimensions(self):
  r=analytics.validate(HEADER+'X,2026-01-01,P,C,R,1,10,5\nX,2026-01-02,P2,C,R,1,10,5\n');self.assertEqual(r['rejected'],1)
 def test_decimal_validation(self):
  self.assertEqual(analytics.money('0.29'),29)
  for value in ['NaN','Infinity','-1','0.001','1000001']:
   with self.assertRaises(ValueError):analytics.money(value)
 def test_no_valid_rows_no_import(self):
  with self.assertRaises(ValueError):analytics.import_csv(self.db,HEADER,'empty','MAD')
  self.assertEqual(self.db.execute('SELECT COUNT(*) FROM imports').fetchone()[0],1)
 def test_empty_filter_and_zero_revenue(self):
  d=analytics.dashboard(self.db,self.batch,{'region':'Missing'});self.assertEqual(d['kpis']['revenue_cents'],0);self.assertIsNone(d['kpis']['margin'])
  batch=analytics.import_csv(self.db,HEADER+'Z,2026-01-01,Gift,Promo,Nador,1,0,2\n','zero','MAD')['id'];d=analytics.dashboard(self.db,batch,{})['kpis'];self.assertIsNone(d['margin']);self.assertEqual(d['profit_cents'],-200)
 def test_csv_formula_safety_and_full_export(self):
  batch=analytics.import_csv(self.db,HEADER+'Z,2026-01-01,=SUM(A1),Promo,Nador,1,2,1\n','formula','MAD')['id'];out=analytics.export_csv(self.db,batch,{});self.assertIn("'=SUM(A1)",out)
  out=analytics.export_csv(self.db,self.batch,{'region':'Nador'});self.assertEqual(len(out.splitlines()),4)
 def test_invalid_range_and_schema(self):
  with self.assertRaises(ValueError):analytics.dashboard(self.db,self.batch,{'start':'2026-02-01','end':'2026-01-01'})
  with self.assertRaises(ValueError):analytics.validate('wrong,columns\nx,y\n')
 def test_isolated_snapshots(self):
  other=analytics.import_csv(self.db,DATA,'copy','EUR')['id'];self.assertNotEqual(other,self.batch);self.assertEqual(analytics.dashboard(self.db,other,{})['kpis']['orders'],3)
 def test_data_survives_reopen(self):
  self.db.close();self.db=analytics.connect(self.path);self.assertEqual(analytics.dashboard(self.db,self.batch,{})['kpis']['lines'],4)

class HttpTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory();server.DB=Path(cls.temp.name)/'api.db';analytics.initialize(server.DB);cls.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start()
 @classmethod
 def tearDownClass(cls):cls.http.shutdown();cls.http.server_close();cls.thread.join();cls.temp.cleanup()
 def request(self,path,method='GET',body=None,token=None,host=None):
  conn=http.client.HTTPConnection('127.0.0.1',self.http.server_port);headers={'Content-Type':'application/json'}
  if token:headers['X-CSRF-Token']=token
  if host:headers['Host']=host
  conn.request(method,path,json.dumps(body) if body is not None else None,headers);r=conn.getresponse();raw=r.read();result=json.loads(raw) if 'application/json' in r.getheader('Content-Type','') else raw.decode();conn.close();return r.status,result
 def test_csrf_and_host(self):
  self.assertEqual(self.request('/api/import','POST',{'csv':DATA,'name':'test','currency':'MAD'})[0],403)
  self.assertEqual(self.request('/api/datasets',host='evil.test')[0],403)
 def test_validate_then_import(self):
  _,d=self.request('/api/datasets');payload={'csv':DATA,'name':'api test','currency':'MAD'}
  status,r=self.request('/api/validate','POST',payload,d['token']);self.assertEqual(status,200);self.assertEqual(r['accepted'],4)
  status,r=self.request('/api/import','POST',payload,d['token']);self.assertEqual(status,201)
  status,r=self.request('/api/dashboard?dataset='+str(r['id']));self.assertEqual(status,200);self.assertEqual(r['kpis']['revenue_cents'],55000)
 def test_static_and_bad_json(self):
  self.assertEqual(self.request('/')[0],200);self.assertEqual(self.request('/../analytics.py')[0],404)
  _,d=self.request('/api/datasets');self.assertEqual(self.request('/api/import','POST',[],d['token'])[0],400)
if __name__=='__main__':unittest.main(verbosity=2)
