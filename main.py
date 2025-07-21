from fastapi import FastAPI, HTTPException, Request, Response, status, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
import sqlite3
import os
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
import time

app = FastAPI()

DB_PATH = 'itens.db'

# Prometheus metrics
REQUEST_COUNT = Counter('http_requests_total', 'Total HTTP requests', ['method', 'endpoint', 'http_status'])
REQUEST_LATENCY = Histogram('http_request_duration_seconds', 'HTTP request latency', ['endpoint'])
DB_SIZE = Gauge('sqlite_db_file_size_bytes', 'Size of the SQLite DB file in bytes')
CRUD_OPS = Counter('crud_operations_total', 'Total CRUD operations', ['operation'])

# Pydantic model
class Item(BaseModel):
    id: int | None = None
    name: str
    description: str | None = None

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT
    )''')
    conn.commit()
    conn.close()

init_db()

# Middleware for metrics
@app.middleware('http')
async def prometheus_metrics_middleware(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    endpoint = request.url.path
    REQUEST_COUNT.labels(request.method, endpoint, response.status_code).inc()
    REQUEST_LATENCY.labels(endpoint).observe(process_time)
    # Update DB size gauge
    if os.path.exists(DB_PATH):
        DB_SIZE.set(os.path.getsize(DB_PATH))
    return response

@app.get('/metrics')
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

# CRUD Endpoints
@app.post('/items/', response_model=Item, status_code=status.HTTP_201_CREATED)
def create_item(item: Item, db=Depends(get_db)):
    c = db.cursor()
    c.execute('INSERT INTO items (name, description) VALUES (?, ?)', (item.name, item.description))
    db.commit()
    item_id = c.lastrowid
    CRUD_OPS.labels('create').inc()
    return {"id": item_id, **item.dict()}

@app.get('/items/{item_id}', response_model=Item)
def read_item(item_id: int, db=Depends(get_db)):
    c = db.cursor()
    c.execute('SELECT * FROM items WHERE id = ?', (item_id,))
    row = c.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail='Item not found')
    CRUD_OPS.labels('read').inc()
    return dict(row)

@app.put('/items/{item_id}', response_model=Item)
def update_item(item_id: int, item: Item, db=Depends(get_db)):
    c = db.cursor()
    c.execute('UPDATE items SET name = ?, description = ? WHERE id = ?', (item.name, item.description, item_id))
    db.commit()
    if c.rowcount == 0:
        raise HTTPException(status_code=404, detail='Item not found')
    CRUD_OPS.labels('update').inc()
    return {"id": item_id, **item.dict()}

@app.delete('/items/{item_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, db=Depends(get_db)):
    c = db.cursor()
    c.execute('DELETE FROM items WHERE id = ?', (item_id,))
    db.commit()
    if c.rowcount == 0:
        raise HTTPException(status_code=404, detail='Item not found')
    CRUD_OPS.labels('delete').inc()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@app.get('/items/', response_model=list[Item])
def list_items(db=Depends(get_db)):
    c = db.cursor()
    c.execute('SELECT * FROM items')
    rows = c.fetchall()
    CRUD_OPS.labels('list').inc()
    return [dict(row) for row in rows] 