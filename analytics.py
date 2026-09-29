"""Validation and SQL analytics for a single-currency order-line dataset."""
import csv
import io
import sqlite3
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

COLUMNS = ['order_id','date','product','category','region','quantity','unit_price','unit_cost']
SCHEMA = '''
CREATE TABLE IF NOT EXISTS imports(id INTEGER PRIMARY KEY,name TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,row_count INTEGER NOT NULL,rejected_count INTEGER NOT NULL,currency TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sales(id INTEGER PRIMARY KEY,batch_id INTEGER NOT NULL REFERENCES imports(id),order_id TEXT NOT NULL,day TEXT NOT NULL,product TEXT NOT NULL,category TEXT NOT NULL,region TEXT NOT NULL,quantity INTEGER NOT NULL,price_cents INTEGER NOT NULL,cost_cents INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS sales_batch_day ON sales(batch_id,day);
CREATE INDEX IF NOT EXISTS sales_batch_region ON sales(batch_id,region);
'''

def connect(path):
    db=sqlite3.connect(path);db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON');return db

def initialize(path):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    with connect(path) as db:db.executescript(SCHEMA)

def money(value):
    try:
        n=Decimal(value)
        if not n.is_finite() or n<0 or n>Decimal('1000000') or n*100!=(n*100).to_integral_value():raise ValueError()
        return int(n*100)
    except (InvalidOperation,ValueError,TypeError):raise ValueError('Prices/costs must be non-negative numbers with at most two decimals (max 1,000,000).')

def validate(source):
    reader=csv.DictReader(io.StringIO(source.lstrip('\ufeff')),strict=True)
    if reader.fieldnames!=COLUMNS:raise ValueError('CSV header must be: '+','.join(COLUMNS))
    valid=[];errors=[];seen=set();order_dimensions={}
    for line,row in enumerate(reader,2):
        if line>10001:raise ValueError('Maximum 10,000 rows per dataset.')
        try:
            if None in row or any(v is None for v in row.values()):raise ValueError('Wrong number of columns.')
            row={k:v.strip() for k,v in row.items()}
            for key in ('order_id','product','category','region'):
                if not row[key] or len(row[key])>100:raise ValueError(f'{key} must contain 1–100 characters.')
            day=date.fromisoformat(row['date'])
            if day.isoformat()!=row['date']:raise ValueError('Use YYYY-MM-DD dates.')
            quantity=int(row['quantity'])
            if not 1<=quantity<=100000:raise ValueError('Quantity must be between 1 and 100,000.')
            price,cost=money(row['unit_price']),money(row['unit_cost'])
            item=(row['order_id'],day.isoformat(),row['product'],row['category'],row['region'],quantity,price,cost)
            if item in seen:raise ValueError('Duplicate order line in this file.')
            dimensions=(day.isoformat(),row['region'])
            if row['order_id'] in order_dimensions and order_dimensions[row['order_id']]!=dimensions:raise ValueError('Lines in one order must have the same date and region.')
            seen.add(item);order_dimensions[row['order_id']]=dimensions;valid.append(item)
        except (ValueError,TypeError) as error:errors.append({'line':line,'message':str(error)})
    return {'rows':valid,'accepted':len(valid),'rejected':len(errors),'errors':errors}

def import_csv(db,source,name,currency):
    if currency not in ('MAD','EUR','USD'):raise ValueError('Choose MAD, EUR or USD. No currency conversion is performed.')
    result=validate(source)
    if not result['accepted']:raise ValueError('No valid rows to import.')
    with db:
        batch=db.execute('INSERT INTO imports(name,row_count,rejected_count,currency) VALUES(?,?,?,?)',(name[:100],result['accepted'],result['rejected'],currency)).lastrowid
        db.executemany('INSERT INTO sales(batch_id,order_id,day,product,category,region,quantity,price_cents,cost_cents) VALUES(?,?,?,?,?,?,?,?,?)',[(batch,*r) for r in result['rows']])
    return {'id':batch,**{k:v for k,v in result.items() if k!='rows'}}

def scope(batch,filters):
    clauses=['batch_id=?'];args=[batch]
    for key,col in [('region','region'),('category','category')]:
        if filters.get(key):clauses.append(col+'=?');args.append(filters[key])
    for key,op in [('start','>='),('end','<=')]:
        if filters.get(key):
            value=date.fromisoformat(filters[key]).isoformat();clauses.append('day'+op+'?');args.append(value)
    if filters.get('start') and filters.get('end') and filters['start']>filters['end']:raise ValueError('Start date must not be after end date.')
    return ' WHERE '+' AND '.join(clauses),args

def summary(db,where,args):
    r=dict(db.execute('SELECT COUNT(*) lines,COUNT(DISTINCT order_id) orders,COALESCE(SUM(quantity),0) units,COALESCE(SUM(quantity*price_cents),0) revenue_cents,COALESCE(SUM(quantity*(price_cents-cost_cents)),0) profit_cents FROM sales'+where,args).fetchone())
    r['margin']=round(100*r['profit_cents']/r['revenue_cents'],2) if r['revenue_cents'] else None
    r['average_order_cents']=round(r['revenue_cents']/r['orders']) if r['orders'] else 0
    return r

def dashboard(db,batch,filters):
    dataset=db.execute('SELECT * FROM imports WHERE id=?',(batch,)).fetchone()
    if not dataset:raise ValueError('Dataset not found.')
    where,args=scope(batch,filters);kpis=summary(db,where,args)
    series=[dict(r) for r in db.execute("SELECT substr(day,1,7) month,SUM(quantity*price_cents) revenue_cents,SUM(quantity*(price_cents-cost_cents)) profit_cents FROM sales"+where+' GROUP BY month ORDER BY month',args)]
    groups={}
    for dimension in ('product','category','region'):
        groups[dimension]=[dict(r) for r in db.execute(f'SELECT {dimension} name,SUM(quantity) units,SUM(quantity*price_cents) revenue_cents,SUM(quantity*(price_cents-cost_cents)) profit_cents FROM sales'+where+f' GROUP BY {dimension} ORDER BY revenue_cents DESC,name LIMIT 10',args)]
    rows=[dict(r) for r in db.execute('SELECT * FROM sales'+where+' ORDER BY day DESC,id DESC LIMIT 100',args)]
    previous=None
    if filters.get('start') and filters.get('end'):
        start=date.fromisoformat(filters['start']);end=date.fromisoformat(filters['end']);days=(end-start).days+1
        prev={**filters,'start':(start-timedelta(days=days)).isoformat(),'end':(start-timedelta(days=1)).isoformat()}
        pw,pa=scope(batch,prev);previous={'start':prev['start'],'end':prev['end'],**summary(db,pw,pa)}
        previous['revenue_change_percent']=round(100*(kpis['revenue_cents']-previous['revenue_cents'])/previous['revenue_cents'],2) if previous['revenue_cents'] else None
    choices={d:[r[0] for r in db.execute(f'SELECT DISTINCT {d} FROM sales WHERE batch_id=? ORDER BY {d}',(batch,))] for d in ('region','category')}
    return {'dataset':dict(dataset),'kpis':kpis,'series':series,'groups':groups,'rows':rows,'choices':choices,'previous':previous}

def export_csv(db,batch,filters):
    where,args=scope(batch,filters);out=io.StringIO();writer=csv.writer(out);writer.writerow(COLUMNS)
    def safe(value):
        s=str(value)
        return "'"+s if s[:1] in ('=','+','-','@','\t','\n','\r') else s
    for r in db.execute('SELECT * FROM sales'+where+' ORDER BY day,id',args):writer.writerow([safe(r[k]) for k in ('order_id','day','product','category','region','quantity')]+[f"{r['price_cents']/100:.2f}",f"{r['cost_cents']/100:.2f}"])
    return out.getvalue()
