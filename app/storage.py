import json
import sqlite3
import zlib
from pathlib import Path

from .domain import Bar, iso, now


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS bars(contract TEXT, timeframe INTEGER, end TEXT, payload TEXT, seen_at TEXT,
          PRIMARY KEY(contract,timeframe,end));
        CREATE TABLE IF NOT EXISTS revisions(id INTEGER PRIMARY KEY, contract TEXT, timeframe INTEGER, end TEXT,
          previous TEXT, replacement TEXT, seen_at TEXT);
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, contract TEXT,
          kind TEXT, payload TEXT, recorded_at TEXT);
        CREATE TABLE IF NOT EXISTS quotes(id INTEGER PRIMARY KEY, contract TEXT, received_at TEXT, payload TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, payload TEXT);
        CREATE TABLE IF NOT EXISTS option_records(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,
          root TEXT,kind TEXT,source_at TEXT,recorded_at TEXT,payload BLOB);
        CREATE INDEX IF NOT EXISTS option_records_lookup ON option_records(kind,root,seq);
        ''')

    def save_bars(self, bars, seen_at):
        changed = []
        for b in bars:
            payload = json.dumps(b.json(), sort_keys=True)
            old = self.db.execute('SELECT payload FROM bars WHERE contract=? AND timeframe=? AND end=?', (b.contract, b.timeframe, b.end)).fetchone()
            if old and old[0] != payload:
                self.db.execute('INSERT INTO revisions(contract,timeframe,end,previous,replacement,seen_at) VALUES(?,?,?,?,?,?)', (b.contract,b.timeframe,b.end,old[0],payload,seen_at))
                changed.append(b)
            self.db.execute('INSERT INTO bars VALUES(?,?,?,?,?) ON CONFLICT(contract,timeframe,end) DO UPDATE SET payload=excluded.payload,seen_at=excluded.seen_at', (b.contract,b.timeframe,b.end,payload,seen_at))
        self.db.commit()
        return changed

    def bars(self, code, timeframe):
        rows = self.db.execute('SELECT payload FROM bars WHERE contract=? AND timeframe=? ORDER BY end', (code,timeframe))
        return [Bar(**json.loads(r[0])) for r in rows]

    def event(self, event):
        cur = self.db.execute('INSERT OR IGNORE INTO events(id,contract,kind,payload,recorded_at) VALUES(?,?,?,?,?)', (event['id'],event.get('contract',''),event['kind'],json.dumps(event,ensure_ascii=False),iso(now())))
        self.db.commit()
        return cur.rowcount > 0

    def events(self, limit=150, after=0):
        rows = self.db.execute('SELECT seq,payload FROM events WHERE seq>? ORDER BY seq DESC LIMIT ?', (after,limit))
        return [dict(json.loads(payload), seq=seq) for seq,payload in rows]

    def quote(self, code, q):
        self.db.execute('INSERT INTO quotes(contract,received_at,payload) VALUES(?,?,?)', (code,iso(now()),json.dumps(q)))

    def get(self, key, default=None):
        row = self.db.execute('SELECT payload FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key,json.dumps(value,ensure_ascii=False)))
        self.db.commit()

    def close(self):
        self.db.commit()
        self.db.close()

    def option_record(self, ident, root, kind, payload, source_at=None):
        packed=zlib.compress(json.dumps(payload,ensure_ascii=False,allow_nan=False,sort_keys=True).encode('utf-8'))
        cur=self.db.execute('INSERT OR IGNORE INTO option_records(id,root,kind,source_at,recorded_at,payload) VALUES(?,?,?,?,?,?)',
                            (ident,root,kind,source_at,iso(now()),packed))
        self.db.commit()
        return cur.rowcount>0

    def option_history(self, kind, root=None, limit=100, after=0):
        sql='SELECT seq,id,source_at,recorded_at,payload FROM option_records WHERE kind=? AND seq>?'
        args=[kind,after]
        if root:
            sql+=' AND root=?'; args.append(root)
        sql+=' ORDER BY seq DESC LIMIT ?'; args.append(limit)
        return [dict(seq=s,id=i,source_at=a,recorded_at=r,payload=json.loads(zlib.decompress(p))) for s,i,a,r,p in self.db.execute(sql,args)]
