import argparse
import asyncio
import json
import logging
import os
import time
from collections import Counter
from urllib.parse import urlsplit
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core import URL, product, price, database, getstate, setstate, record, canonical_url, product_excerpt, embedded_asins

from media import send_deal, source_reference
from schedule import DigestSchedule
from resolver import resolve_all

LOG = logging.getLogger('deals')


def log_config(cfg, channel_names=None):
    # Log collector options only; never dump environment variables or credentials.
    options = {
        'destination': cfg.get('destination', 'me'),
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
    LOG.info('========== Collector configuration ==========')
    LOG.info('  Channels (%s):', len(cfg.get('channels', [])))
    for channel in cfg.get('channels', []):
        name = (channel_names or {}).get(str(channel), 'name unavailable')
        LOG.info('    %s (%s)', channel, ' '.join(name.split()))
    for key, value in options.items():
        LOG.info('  %-20s : %s', key, json.dumps(value, ensure_ascii=False))
    LOG.info('  %-20s : %s', 'active_schedule', 'digest_cron' if cfg.get('digest_cron') is not None else 'digest_times')
    LOG.info('=============================================')


def log_product_counts(db):
    total, pending = db.execute('SELECT COUNT(*), COALESCE(SUM(pending),0) FROM deals').fetchone()
    LOG.info('Products collected: %s', total)
    LOG.info('Pending products: %s', pending)

async def resolve(url, http):
    found = await resolve_all(url, http)
    return next(iter(found)) if len(found) == 1 else None

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


async def log_product_sources(client, db, asin, reason):
    selected = db.execute('SELECT source FROM deals WHERE asin=?', (asin,)).fetchone()[0]
    sources = db.execute('SELECT source FROM origins WHERE asin=? ORDER BY source', (asin,)).fetchall()
    channels = {}
    for row in sources:
        ref = source_reference(row['source'])
        peer = str(ref[0]) if ref else 'unknown'
        if peer not in channels:
            name = await channel_name(client, db, row['source'])
            channels[peer] = {'name': name, 'posts': 0}
        channels[peer]['posts'] += 1
    summary = '; '.join(f"{peer} ({info['name']}): {info['posts']} post(s)" for peer, info in channels.items())
    selected_ref = source_reference(selected)
    selected_peer = selected_ref[0] if selected_ref else 'unknown'
    selected_name = await channel_name(client, db, selected)
    LOG.info('Product %s | Duplicate posts found: %s | Sources: %s | Selected channel: %s (%s) | Selection: %s',
             asin, max(0, len(sources)-1), summary, selected_peer, selected_name, reason)


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
                 product_excerpt(row['body']), canonical_url(row['asin']),
                 'Original posts:', *[s[0] for s in sources],
                 'Check variant, seller, coupon, Prime and checkout price.'])
        # Delivery is at-least-once: a crash after sending may repeat one entry.
        await send_deal(client, body, row['source'], row['asin'], channel_name=name, destination=cfg.get('destination', 'me'))
        db.execute('UPDATE deals SET pending=0,notified=1,last_sent=cents WHERE asin=?',(row['asin'],))
        db.commit()
        sent_count += 1
        await asyncio.sleep(1)
    LOG.info('%s delivery: %s products sent.', 'Watchlist' if watch_only else 'Digest', sent_count)

async def poll_channel(client, db, cfg, http, channel, observed):
    from telethon import utils
    from telethon.tl.types import MessageEntityTextUrl
    entity = await client.get_entity(channel)
    chatid = utils.get_peer_id(entity)
    name = ' '.join((getattr(entity, 'title', None) or getattr(entity, 'username', None) or str(chatid)).split())
    setstate(db, f'channel_name:{chatid}', name)
    if getattr(entity, 'username', None):
        setstate(db, f'channel_name:{entity.username}', name)
    key = f'cursor:{chatid}'
    cursor = getstate(db,key)
    if cursor is None:
        latest = await client.get_messages(entity,limit=1)
        setstate(db,key,latest[0].id if latest else 0)
        db.commit()
        LOG.info('Channel %s (%s) | Initialised at message %s; collecting new posts from now.',
                 chatid, name, getstate(db,key))
        return
    start_cursor = cursor
    counts = Counter()
    unrecognised_hosts = Counter()
    async for msg in client.iter_messages(entity,min_id=int(cursor),reverse=True):
        counts['scanned'] += 1
        text = msg.raw_text or ''
        urls = URL.findall(text)
        urls.extend(e.url for e in (msg.entities or []) if isinstance(e,MessageEntityTextUrl))
        urls.extend(b.url for line in (msg.buttons or []) for b in line if b.url)
        asins = set()
        for url in set(urls):
            counts['links'] += 1
            cleaned = url.rstrip(').,;!')
            linked = embedded_asins(cleaned)
            if not linked:
                linked = await resolve_all(cleaned,http)
            if linked:
                asins.update(linked)
            else:
                # Hostnames only: never print affiliate query strings or full URLs.
                try:
                    host = urlsplit(cleaned).hostname
                except ValueError:
                    host = None
                unrecognised_hosts[host or 'invalid-url'] += 1
        if not asins:
            counts['no_product'] += 1
        else:
            counts['recognised_posts'] += 1
            folded = text.casefold()
            include = cfg.get('include_keywords',[])
            exclude = cfg.get('exclude_keywords',[])
            accepted = (not include or any(w.casefold() in folded for w in include)) and not any(w.casefold() in folded for w in exclude)
            if not accepted:
                counts['keyword_filtered'] += 1
            else:
                cents = price(text) if len(asins)==1 else None
                maximum = cfg.get('max_price_eur')
                if maximum is not None and cents is not None and cents > maximum*100:
                    counts['price_filtered'] += 1
                else:
                    counts['accepted_posts'] += 1
                    if getattr(entity,'username',None):
                        source = f'https://t.me/{entity.username}/{msg.id}'
                    else:
                        source = f'https://t.me/c/{entity.id}/{msg.id}'
                    for asin in asins:
                        if db.execute('SELECT 1 FROM deals WHERE asin=?',(asin,)).fetchone() is None:
                            counts['new_products'] += 1
                        counts['product_matches'] += 1
                        observed[asin] = record(db,asin,text,source,cents,int(time.time()),cfg['price_drop_percent'])
        setstate(db,key,msg.id)
        db.commit()
    LOG.info('Channel %s (%s) | Messages scanned: %s | Links inspected: %s | Posts with recognised Amazon products: %s | Accepted posts: %s | Product matches: %s | New products: %s | No recognised product: %s | Keyword filtered: %s | Price filtered: %s | Cursor: %s -> %s',
             chatid, name, counts['scanned'], counts['links'], counts['recognised_posts'],
             counts['accepted_posts'], counts['product_matches'], counts['new_products'],
             counts['no_product'], counts['keyword_filtered'], counts['price_filtered'],
             start_cursor, getstate(db,key))
    if unrecognised_hosts:
        LOG.info('Channel %s (%s) | Unrecognised/unresolved link hosts: %s', chatid, name,
                 ', '.join(f'{host}: {count}' for host,count in sorted(unrecognised_hosts.items())))


async def poll(client, db, cfg, http):
    observed = {}
    succeeded = failed = 0
    for channel in cfg['channels']:
        try:
            await poll_channel(client, db, cfg, http, channel, observed)
            succeeded += 1
        except Exception as exc:
            LOG.error('Channel %s | Scan failed: %s; saved cursor retained for retry.', channel, type(exc).__name__)
            if type(exc).__name__ == 'FloodWaitError':
                # Do not continue issuing calls while Telegram requires a wait.
                await asyncio.sleep(max(1, getattr(exc, 'seconds', 60)))
                raise
            failed += 1
    LOG.info('Channel scans completed: %s/%s successful, %s failed.', succeeded, len(cfg['channels']), failed)
    for asin, reason in observed.items():
        await log_product_sources(client, db, asin, reason)
    db.commit()

def destination_value(cfg):
    value = cfg.get('destination', 'me')
    if value == 'me':
        return value
    if isinstance(value, str) and value.startswith('-100') and value[1:].isdigit():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or not str(value).startswith('-100'):
        raise ValueError('destination must be "me" or a numeric channel ID beginning -100')
    if str(value) in {str(channel) for channel in cfg.get('channels', [])}:
        raise ValueError('The destination channel must not also be a source channel')
    return value


async def validate_destination(client, cfg):
    destination = destination_value(cfg)
    if destination == 'me':
        return destination
    entity = await client.get_entity(destination)
    rights = getattr(entity, 'admin_rights', None)
    if not getattr(entity, 'broadcast', False):
        raise ValueError('destination must be a Telegram channel')
    if not (getattr(entity, 'creator', False) or (rights and getattr(rights, 'post_messages', False))):
        raise ValueError('The logged-in Telegram account needs permission to post in the destination channel')
    name = ' '.join((getattr(entity, 'title', None) or str(destination)).split())
    LOG.info('Destination channel: %s (%s)', destination, name)
    return destination


async def main(command):
    from telethon import TelegramClient
    import aiohttp
    os.umask(0o077)
    Path('/data').mkdir(exist_ok=True)
    if command == 'run':
        cfg = json.loads(Path('/app/config.json').read_text())
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
        names = {}
        for channel in cfg.get('channels', []):
            try:
                entity = await client.get_entity(channel)
                names[str(channel)] = getattr(entity, 'title', None) or getattr(entity, 'username', None) or str(channel)
            except Exception as exc:
                names[str(channel)] = 'name unavailable'
                LOG.warning('Channel %s name lookup failed: %s', channel, type(exc).__name__)
        log_config(cfg, names)
        cfg['destination'] = await validate_destination(client, cfg)
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
