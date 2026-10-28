import re
import sqlite3
from urllib.parse import urlsplit
from decimal import Decimal

URL = re.compile(r'https?://[^\s<>]+', re.I)
ASIN = re.compile(r'/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:[/?#]|$)', re.I)
PRICE = re.compile(r'(?<![\d.,])(\d{1,6}(?:[.,]\d{3})*(?:[.,]\d{2})?)\s*€|€\s*(\d{1,6}(?:[.,]\d{3})*(?:[.,]\d{2})?)')

def product(url):
    u = urlsplit(url)
    host = (u.hostname or '').lower()
    if host != 'amazon.es' and not host.endswith('.amazon.es'):
        return None
    m = ASIN.search(u.path)
    return m[1].upper() if m else None

def price(text):
    values = []
    for m in PRICE.finditer(text):
        s = m[1] or m[2]
        if len(s) >= 3 and s[-3] in ',.':
            s = s[:-3].replace('.', '').replace(',', '') + '.' + s[-2:]
        else:
            s = s.replace('.', '').replace(',', '')
        values.append(int(Decimal(s) * 100))
    # Multiple numbers may mean list price, coupon, shipping or multiple products.
    return values[0] if len(values) == 1 else None

def database(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript('''
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY,v TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS deals(
      asin TEXT PRIMARY KEY, body TEXT, source TEXT, cents INTEGER,
      notified INTEGER DEFAULT 0, pending INTEGER DEFAULT 1,
      last_sent INTEGER, updated INTEGER);
    CREATE TABLE IF NOT EXISTS origins(
      asin TEXT, source TEXT, PRIMARY KEY(asin,source));
    ''')
    return db

def getstate(db, key, default=None):
    row = db.execute('SELECT v FROM state WHERE k=?',(key,)).fetchone()
    return row[0] if row else default

def setstate(db, key, value):
    db.execute('INSERT OR REPLACE INTO state VALUES (?,?)',(key,str(value)))

def record(db, asin, body, source, cents, now, drop):
    row = db.execute('SELECT * FROM deals WHERE asin=?',(asin,)).fetchone()
    db.execute('INSERT OR IGNORE INTO origins VALUES (?,?)',(asin,source))
    if row is None:
        db.execute('INSERT INTO deals(asin,body,source,cents,updated) VALUES(?,?,?,?,?)',
                   (asin,body,source,cents,now))
    else:
        # Keep the cheapest unambiguous advertised offer; an unknown price cannot overwrite it.
        better = cents is not None and (row['cents'] is None or cents < row['cents'])
        pending = row['pending'] or (row['notified'] and better and row['last_sent'] is not None
                    and cents <= row['last_sent'] * (1-drop/100))
        if better:
            db.execute('UPDATE deals SET body=?,source=?,cents=?,pending=?,updated=? WHERE asin=?',
                       (body,source,cents,int(pending),now,asin))
        else:
            db.execute('UPDATE deals SET updated=? WHERE asin=?',(now,asin))
