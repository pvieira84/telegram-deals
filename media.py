"""Retrieve original Telegram images without storing expiring file references."""
import logging
import re
from urllib.parse import urlsplit

LOG = logging.getLogger('deals')


def source_reference(source):
    u = urlsplit(source)
    if u.scheme != 'https' or u.hostname != 't.me':
        return None
    parts = u.path.strip('/').split('/')
    if len(parts) == 3 and parts[0] == 'c' and all(p.isdigit() for p in parts[1:]):
        return int('-100' + parts[1]), int(parts[2])
    if len(parts) == 2 and re.fullmatch(r'[A-Za-z0-9_]+', parts[0]) and parts[1].isdigit():
        return parts[0], int(parts[1])
    return None


def is_image(message):
    document = getattr(message, 'document', None)
    return bool(getattr(message, 'photo', None) or
                (document and getattr(document, 'mime_type', '').startswith('image/')))


async def original_images(client, source):
    ref = source_reference(source)
    if ref is None:
        return []
    peer, message_id = ref
    try:
        entity = await client.get_entity(peer)
        if getattr(entity, 'noforwards', False):
            LOG.info('Source restricts saving media; keeping original-post link.')
            return []
        message = await client.get_messages(entity, ids=message_id)
        if not message or getattr(message, 'noforwards', False):
            return []
        group = getattr(message, 'grouped_id', None)
        if group:
            # Telegram albums have at most ten media items. Fetch a bounded
            # neighbourhood, then match the exact album ID, never adjacent posts.
            nearby = await client.get_messages(entity, ids=list(range(max(1,message_id-10),message_id+11)))
            messages = sorted((m for m in nearby if m and getattr(m,'grouped_id',None)==group),key=lambda m:m.id)
        else:
            messages = [message]
        return [m.media for m in messages if is_image(m) and not getattr(m,'noforwards',False)][:10]
    except Exception as exc:
        # Deleted posts, lost source access and unavailable images must not
        # prevent the deal text being delivered.
        LOG.warning('Original images unavailable (%s); sending text and source link.',type(exc).__name__)
        return []


def utf16_length(text):
    return len(text.encode('utf-16-le')) // 2


async def send_deal(client, body, source, asin):
    images = await original_images(client,source)
    if images:
        compact = f'Deal • https://www.amazon.es/dp/{asin}\nOriginal post: {source}'
        caption = body if utf16_length(body) <= 1024 else compact
        try:
            sent = await client.send_file('me', images, caption=caption, parse_mode=None)
        except Exception as exc:
            if type(exc).__name__ not in {'ChatForwardsRestrictedError','FileReferenceExpiredError','MediaEmptyError'}:
                raise
            LOG.warning('Telegram could not reuse original images (%s); sending text.',type(exc).__name__)
        else:
            if caption != body:
                first = sent[0] if isinstance(sent,(list,tuple)) else sent
                await client.send_message('me',body,parse_mode=None,link_preview=False,reply_to=first.id)
            return
    await client.send_message('me',body,parse_mode=None,link_preview=False)
