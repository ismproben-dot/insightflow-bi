"""Local-only InsightFlow BI server. Python 3.11+, no third-party packages."""
import argparse,csv,json,os,secrets
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
import analytics
ROOT=Path(__file__).resolve().parent
DB=Path(os.environ.get('INSIGHTFLOW_DB',ROOT/'data'/'insightflow.sqlite3'))
TOKEN=secrets.token_urlsafe(32)
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self,status,data,mime='application/json'):
        raw=json.dumps(data).encode() if mime=='application/json' else data.encode() if isinstance(data,str) else data
        self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'");self.end_headers();self.wfile.write(raw)
    def do_GET(self):self.route()
    def do_POST(self):self.route()
    def route(self):
        if self.headers.get('Host') not in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'):return self.send(403,{'error':'Local host required.'})
        try:
            path=urlsplit(self.path).path
            if not path.startswith('/api/'):
                allowed={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript'),'/style.css':('style.css','text/css')}
                if self.command!='GET' or path not in allowed:return self.send(404,{'error':'Not found.'})
                name,mime=allowed[path];return self.send(200,(ROOT/'static'/name).read_bytes(),mime)
            with analytics.connect(DB) as db:
                if self.command=='GET':
                    if path=='/api/datasets':return self.send(200,{'token':TOKEN,'datasets':[dict(r) for r in db.execute('SELECT * FROM imports ORDER BY id DESC')]})
                    q={k:v[0] for k,v in parse_qs(urlsplit(self.path).query).items()}
                    batch=int(q.get('dataset','0'))
                    if path=='/api/dashboard':return self.send(200,analytics.dashboard(db,batch,q))
                    if path=='/api/export':return self.send(200,analytics.export_csv(db,batch,q),'text/csv; charset=utf-8')
                if self.command=='POST':
                    if self.headers.get('X-CSRF-Token')!=TOKEN:return self.send(403,{'error':'Refresh the page before importing.'})
                    if self.headers.get('Content-Type')!='application/json':return self.send(415,{'error':'JSON required.'})
                    length=int(self.headers.get('Content-Length',0))
                    if not 0<length<=2_000_000:return self.send(413,{'error':'Maximum request size: 2 MB.'})
                    d=json.loads(self.rfile.read(length))
                    if not isinstance(d,dict):raise ValueError('JSON object required.')
                    if path=='/api/demo':source=(ROOT/'samples'/'demo-sales.csv').read_text();name='Demo retail dataset';currency='MAD'
                    else:
                        source=d.get('csv');name=d.get('name');currency=d.get('currency')
                        if not isinstance(source,str) or not isinstance(name,str) or not name.strip():raise ValueError('CSV text and dataset name required.')
                    if path=='/api/validate':
                        r=analytics.validate(source);return self.send(200,{k:v for k,v in r.items() if k!='rows'})
                    if path in ('/api/import','/api/demo'):return self.send(201,analytics.import_csv(db,source,name,currency))
            return self.send(404,{'error':'Not found.'})
        except (ValueError,OverflowError,csv.Error,UnicodeError) as e:self.send(400,{'error':str(e)})
        except Exception:self.send(500,{'error':'Internal error. Check your local database and restart.'})
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8080);args=parser.parse_args();analytics.initialize(DB);print(f'InsightFlow BI: http://127.0.0.1:{args.port}');ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
if __name__=='__main__':main()
