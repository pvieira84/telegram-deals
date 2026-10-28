import re
import sqlite3
from urllib.parse import urlsplit, unquote
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

def embedded_asins(url):
    """Find likely product IDs in a URL without visiting the linked website.

    Generic links require the common B0 + eight alphanumeric form. Explicit
    asin parameters and Amazon-style product paths also accept ten-character
    ASINs. Only path/query/fragment are searched, never hostnames or credentials.
    """
    try:
        u = urlsplit(url)
    except ValueError:
        return set()
    if u.scheme.lower() not in {'http', 'https'} or not u.hostname:
        return set()
    payload = u.path + '?' + u.query + '#' + u.fragment
    for _ in range(3):
        decoded = unquote(payload)
        if decoded == payload:
            break
        payload = decoded
    found = set(re.findall(r'(?<![A-Z0-9])(B0[A-Z0-9]{8})(?![A-Z0-9])', payload, re.I))
    found.update(ASIN.findall(payload))
    # Explicit asin fields can contain older ASINs or book identifiers too.
    for match in re.finditer(r'(?:^|[?&#])asin=([A-Z0-9]{10})(?=$|[&#])', payload, re.I):
        found.add(match[1])
    return {asin.upper() for asin in found}


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
        reason = 'first post seen'
        db.execute('INSERT INTO deals(asin,body,source,cents,updated) VALUES(?,?,?,?,?)',
                   (asin,body,source,cents,now))
    else:
        # Keep the cheapest unambiguous advertised offer; an unknown price cannot overwrite it.
        better = cents is not None and (row['cents'] is None or cents < row['cents'])
        pending = row['pending'] or (row['notified'] and better and row['last_sent'] is not None
                    and cents <= row['last_sent'] * (1-drop/100))
        if better:
            reason = 'lower advertised price' if row['cents'] is not None else 'first unambiguous advertised price'
            db.execute('UPDATE deals SET body=?,source=?,cents=?,pending=?,updated=? WHERE asin=?',
                       (body,source,cents,int(pending),now,asin))
        else:
            reason = 'retained selected post: no lower unambiguous price'
            db.execute('UPDATE deals SET updated=? WHERE asin=?',(now,asin))

    return reason


def canonical_url(asin):
    return f'https://www.amazon.es/dp/{asin}'


def product_excerpt(text):
    """Keep the post text, with retailer URLs replaced by our canonical link below."""
    def replace(match):
        url = match.group(0)
        host = (urlsplit(url).hostname or '').lower()
        if embedded_asins(url) or host in {'amzn.eu', 'amzn.to'} or re.fullmatch(r'(?:[a-z0-9-]+\.)?amazon\.[a-z.]+', host):
            return '[Amazon link below]'
        return url
    return URL.sub(replace, text)[:900]
