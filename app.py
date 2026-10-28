import argparse
import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core import URL, product, price, database, getstate, setstate, record

from media import send_deal, source_reference
from schedule import DigestSchedule

LOG = logging.getLogger('deals')


def log_config(cfg):
    # Log collector options only; never dump environment variables or credentials.
    options = {
        'channels': cfg.get('channels', []),
        'timezone': cfg.get('timezone'),
        'digest_cron': cfg.get('digest_cron'),
        'digest_times': cfg.get('digest_times', ['12:30', '20:30']) if cfg.get('digest_cron') is None else cfg.get('digest_times'),
        'poll_seconds': max(30, cfg.get('poll_seconds', 120)),
        'price_drop_percent': cfg.get('price_drop_percent'),
        'include_keywords': cfg.get('include_keywords', []),
        'exclude_keywords': cfg.get('exclude_keywords', []),
        'watch_keywords': cfg.get('watch_keywords', []),
        'max_price_eur': cfg.get('max_price_eur'),
    }
    LOG.info('Collector configuration:')
    for key, value in options.items():
        LOG.info('  %s: %s', key, json.dumps(value, ensure_ascii=False))
    LOG.info('Active digest schedule: %s', 'digest_cron' if cfg.get('digest_cron') is not None else 'digest_times')


def log_product_counts(db):
    total, pending = db.execute('SELECT COUNT(*), COALESCE(SUM(pending),0) FROM deals').fetchone()
    LOG.info('Products collected: %s', total)
    LOG.info('Pending products: %s', pending)

async def resolve(url, http):
    # Follow only explicitly configured retail shorteners. No arbitrary URL fetching.
    from urllib.parse import urlsplit, urljoin
    current = url
    for _ in range(6):
        asin = product(current)
        if asin:
            return asin
        u = urlsplit(current)
        if u.scheme != 'https' or u.port not in (None,443) or u.username or u.password:
            return None
        if (u.hostname or '').lower() not in {'amzn.eu','amzn.to','www.amazon.es','amazon.es'}:
            return None
        try:
            async with http.get(current, allow_redirects=False) as response:
                if response.status not in (301,302,303,307,308):
                    return None
                current = urljoin(current, response.headers.get('Location',''))
        except Exception:
            LOG.warning('A retail link could not be resolved (details omitted).')
            return None
    return None

async def channel_name(client, db, source):
    ref = source_reference(source)
    if ref is None:
        return 'Unknown channel'
    peer, _ = ref
    name = getstate(db, f'channel_name:{peer}')
    if name is None:
        try:
            entity = await client.get_entity(peer)
            name = getattr(entity, 'title', None) or getattr(entity, 'username', None) or str(peer)
            setstate(db, f'channel_name:{peer}', name)
        except Exception:
            name = str(peer)
    return ' '.join(name.split())


async def send_pending(client, db, cfg, watch_only=False):
    watch = [x.casefold() for x in cfg.get('watch_keywords',[])]
    rows = db.execute('SELECT * FROM deals WHERE pending=1 ORDER BY updated').fetchall()
    sent_count = 0
    for row in rows:
        if watch_only and not any(w in row['body'].casefold() for w in watch):
            continue
        sources = db.execute('SELECT source FROM origins WHERE asin=? LIMIT 4',(row['asin'],)).fetchall()
        amount = f"€{row['cents']/100:.2f} advertised" if row['cents'] is not None else 'Price unclear—check original post'
        name = await channel_name(client, db, row['source'])
        body = '\n'.join([f"{'Watchlist' if watch_only else 'Deal'} • {amount}",
                 f'Channel: {name}',
                 row['body'][:900], f"https://www.amazon.es/dp/{row['asin']}",
                 'Original posts:', *[s[0] for s in sources],
                 'Check variant, seller, coupon, Prime and checkout price.'])
        # Delivery is at-least-once: a crash after sending may repeat one entry.
        await send_deal(client, body, row['source'], row['asin'], channel_name=name)
        db.execute('UPDATE deals SET pending=0,notified=1,last_sent=cents WHERE asin=?',(row['asin'],))
        db.commit()
        sent_count += 1
        await asyncio.sleep(1)
    LOG.info('%s delivery: %s products sent.', 'Watchlist' if watch_only else 'Digest', sent_count)

async def poll(client, db, cfg, http):
    from telethon import utils
    from telethon.tl.types import MessageEntityTextUrl
    for channel in cfg['channels']:
        entity = await client.get_entity(channel)
        chatid = utils.get_peer_id(entity)
        name = getattr(entity, 'title', None) or getattr(entity, 'username', None) or str(chatid)
        setstate(db, f'channel_name:{chatid}', name)
        if getattr(entity, 'username', None):
            setstate(db, f'channel_name:{entity.username}', name)
        key = f'cursor:{chatid}'
        cursor = getstate(db,key)
        if cursor is None:
            latest = await client.get_messages(entity,limit=1)
            setstate(db,key,latest[0].id if latest else 0)
            db.commit()
            LOG.info('Initialised source %s; collecting new posts from now.',chatid)
            continue
        async for msg in client.iter_messages(entity,min_id=int(cursor),reverse=True):
            text = msg.raw_text or ''
            urls = URL.findall(text)
            urls.extend(e.url for e in (msg.entities or []) if isinstance(e,MessageEntityTextUrl))
            urls.extend(b.url for line in (msg.buttons or []) for b in line if b.url)
            asins = set()
            for url in set(urls):
                asin = await resolve(url.rstrip(').,;!'),http)
                if asin:
                    asins.add(asin)
            folded = text.casefold()
            include = cfg.get('include_keywords',[])
            exclude = cfg.get('exclude_keywords',[])
            accepted = (not include or any(w.casefold() in folded for w in include)) and not any(w.casefold() in folded for w in exclude)
            if accepted:
                cents = price(text) if len(asins)==1 else None
                maximum = cfg.get('max_price_eur')
                if maximum is None or cents is None or cents <= maximum*100:
                    if getattr(entity,'username',None):
                        source = f'https://t.me/{entity.username}/{msg.id}'
                    else:
                        source = f'https://t.me/c/{entity.id}/{msg.id}'
                    for asin in asins:
                        record(db,asin,text,source,cents,int(time.time()),cfg['price_drop_percent'])
            setstate(db,key,msg.id)
            db.commit()

async def main(command):
    from telethon import TelegramClient
    import aiohttp
    os.umask(0o077)
    Path('/data').mkdir(exist_ok=True)
    if command == 'run':
        cfg = json.loads(Path('/app/config.json').read_text())
        log_config(cfg)
    api_id = int(os.environ['TG_API_ID'])
    client = TelegramClient('/data/telegram',api_id,os.environ['TG_API_HASH'],flood_sleep_threshold=60)
    if command == 'login':
        await client.start()
        print('Authorised. Session saved in the Docker data volume.')
        await client.disconnect()
        return
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError('Run the interactive login command first.')
        if command == 'channels':
            async for dialog in client.iter_dialogs():
                if dialog.is_channel or dialog.is_group:
                    print(f'{dialog.id}\t{dialog.name}')
            return
        if not cfg['channels']:
            raise ValueError('Set at least one channel ID in config.json.')
        if not 0 < cfg['price_drop_percent'] < 100:
            raise ValueError('price_drop_percent must be between 0 and 100.')
        schedule = DigestSchedule(cfg)
        zone = ZoneInfo(cfg['timezone'])
        db = database('/data/deals.sqlite')
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            while True:
                try:
                    await poll(client,db,cfg,http)
                    await send_pending(client,db,cfg,watch_only=True)
                    now = datetime.now(zone)
                    # Catch up a missed slot on restart; send only latest due slot.
                    slot = schedule.latest_due(now)
                    if slot:
                        if getstate(db,'digest_slot') != slot:
                            await send_pending(client,db,cfg)
                            LOG.info('Digest slot %s processed.', slot)
                            setstate(db,'digest_slot',slot)
                            db.commit()
                    LOG.info('Collection cycle completed.')
                except Exception as exc:
                    LOG.error('Cycle failed: %s; retrying next cycle.',type(exc).__name__)
                finally:
                    log_product_counts(db)
                await asyncio.sleep(max(30,cfg.get('poll_seconds',120)))
    finally:
        await client.disconnect()

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    logging.getLogger('telethon').setLevel(logging.WARNING)
    p = argparse.ArgumentParser()
    p.add_argument('command',choices=['login','channels','run'])
    asyncio.run(main(p.parse_args().command))
