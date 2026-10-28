"""Bot API delivery; source reading stays on the personal Telegram client."""
import asyncio
import json
import logging
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

LOG = logging.getLogger('deals')
MAX_IMAGE_BYTES = 10 * 1024 * 1024


class BotAPIError(RuntimeError):
    def __init__(self, code):
        self.code = code
        hint = {401: 'check TG_BOT_TOKEN', 403: 'check bot channel membership and posting permission',
                429: 'Telegram rate limit; delivery will retry later'}.get(code, 'check destination and bot configuration')
        super().__init__(f'Bot API error {code}: {hint}')


class BotMediaUnavailable(RuntimeError):
    pass


class BotDelivery:
    def __init__(self, reader, http, token):
        self.reader, self.http = reader, http
        if not token or not token.strip():
            raise ValueError('Bot delivery requires TG_BOT_TOKEN in the server environment')
        self._token = token.strip()

    async def request(self, method, payload=None, files=None):
        import aiohttp
        for attempt in range(3):
            try:
                # Rebuild multipart bodies and reopen files for every rate-limit retry.
                with ExitStack() as handles:
                    if files:
                        form = aiohttp.FormData()
                        for key,value in (payload or {}).items():
                            form.add_field(key,str(value))
                        for key,path in files.items():
                            form.add_field(key,handles.enter_context(open(path,'rb')),
                                           filename=Path(path).name,content_type='application/octet-stream')
                        kwargs = {'data': form}
                    else:
                        kwargs = {'json': payload or {}}
                    async with self.http.post(f'https://api.telegram.org/bot{self._token}/{method}',
                                              allow_redirects=False,timeout=aiohttp.ClientTimeout(total=120),**kwargs) as response:
                        result = await response.json()
                if result.get('ok'):
                    return result['result']
                code = result.get('error_code',response.status)
                if code == 429 and attempt < 2:
                    delay = max(1,int(result.get('parameters',{}).get('retry_after',30)))
                    LOG.warning('Bot delivery rate-limited; waiting %s seconds.',delay)
                    await asyncio.sleep(delay)
                    continue
                LOG.error('Bot API method %s failed with code %s.', method, code)
                raise BotAPIError(code)
            except BotAPIError:
                raise
            except Exception:
                # HTTP exceptions can include the request URL, which contains a token.
                raise RuntimeError('Bot API request failed; delivery remains pending') from None
        raise BotAPIError(429)

    async def validate(self, destination):
        if destination == 'me':
            raise ValueError('Bot delivery requires a destination channel ID, not "me"')
        me = await self.request('getMe')
        chat = await self.request('getChat',{'chat_id':destination})
        if chat.get('type') != 'channel':
            raise ValueError('Bot destination must be a Telegram channel')
        member = await self.request('getChatMember',{'chat_id':destination,'user_id':me['id']})
        if member.get('status') != 'creator' and not (member.get('status') == 'administrator' and member.get('can_post_messages')):
            raise ValueError('Add the bot as a destination-channel administrator with Post Messages permission')
        LOG.info('Delivery mode: bot (@%s)',me.get('username','unknown'))
        LOG.info('Destination channel: %s (%s)',destination,' '.join(chat.get('title',str(destination)).split()))

    async def send_message(self, destination, body, *, parse_mode=None, link_preview=False, reply_to=None):
        payload = {'chat_id':destination,'text':body,'link_preview_options':{'is_disabled':not link_preview}}
        if reply_to is not None:
            payload['reply_parameters'] = {'message_id':reply_to}
        result = await self.request('sendMessage',payload)
        return SimpleNamespace(id=result['message_id'])

    async def send_file(self, destination, images, *, caption, parse_mode=None):
        # Account-specific Telegram media references cannot be shared with the
        # bot. Download with the reader, then upload temporary bytes as the bot.
        with TemporaryDirectory(prefix='deal-images-') as directory:
            paths = []
            for i,image in enumerate(images):
                path = Path(directory)/f'photo-{i}.jpg'
                size = 0
                try:
                    with path.open('wb') as output:
                        async for chunk in self.reader.iter_download(image,request_size=65536):
                            size += len(chunk)
                            if size > MAX_IMAGE_BYTES:
                                raise BotMediaUnavailable('Image exceeds the bot photo upload size limit')
                            output.write(chunk)
                except Exception:
                    raise BotMediaUnavailable('Original image could not be downloaded for bot delivery') from None
                if not size:
                    raise BotMediaUnavailable('Original image is empty')
                paths.append(path)
            if not paths:
                raise BotMediaUnavailable('No original images available')
            try:
                if len(paths)==1:
                    result = await self.request('sendPhoto',{'chat_id':destination,'caption':caption,
                                                           'photo':'attach://photo0'}, {'photo0':paths[0]})
                    return [SimpleNamespace(id=result['message_id'])]
                media = [{'type':'photo','media':f'attach://photo{i}',**({'caption':caption} if i==0 else {})}
                         for i in range(len(paths))]
                result = await self.request('sendMediaGroup',{'chat_id':destination,'media':json.dumps(media)},
                                            {f'photo{i}':path for i,path in enumerate(paths)})
                return [SimpleNamespace(id=item['message_id']) for item in result]
            except BotAPIError as exc:
                if exc.code == 400:
                    raise BotMediaUnavailable('Telegram rejected the photo upload') from None
                raise
