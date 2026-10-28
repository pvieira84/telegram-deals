import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock, patch

from bot_delivery import BotDelivery, BotAPIError, BotMediaUnavailable
from core import database,record,setstate
from app import send_pending
from media import send_deal

class Reader:
    async def iter_download(self, image, request_size):
        yield b'photo-bytes'

class Response:
    def __init__(self,result,status=200): self.result=result;self.status=status
    async def __aenter__(self): return self
    async def __aexit__(self,*args): pass
    async def json(self): return self.result

class HTTP:
    def __init__(self,responses): self.responses=iter(responses);self.calls=[]
    def post(self,url,**kwargs):
        self.calls.append((url,kwargs))
        return next(self.responses)

class BotDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def bot(self,reader=None):
        return BotDelivery(reader or Reader(),Obj(),'test-token')

    def test_requires_token(self):
        with self.assertRaises(ValueError): BotDelivery(Reader(),Obj(),'')

    async def test_validates_bot_posting_rights(self):
        bot=self.bot();bot.request=AsyncMock(side_effect=[{'id':5,'username':'deal_bot'},
            {'type':'channel','title':'Private deals'}, {'status':'administrator','can_post_messages':True}])
        with self.assertLogs('deals',level='INFO') as logs:
            await bot.validate(-100123)
        self.assertIn('Private deals','\n'.join(logs.output))
        self.assertNotIn('test-token','\n'.join(logs.output))
        self.assertEqual(bot.request.call_args.args[1],{'chat_id':-100123,'user_id':5})

    async def test_rejects_saved_messages_and_missing_rights(self):
        bot=self.bot()
        with self.assertRaises(ValueError): await bot.validate('me')
        bot.request=AsyncMock(side_effect=[{'id':5},{'type':'channel'},{'status':'member'}])
        with self.assertRaises(ValueError): await bot.validate(-100123)

    async def test_text_reply_destination(self):
        bot=self.bot();bot.request=AsyncMock(return_value={'message_id':9})
        result=await bot.send_message(-100123,'Deal',reply_to=7)
        method,payload=bot.request.call_args.args
        self.assertEqual(method,'sendMessage');self.assertEqual(payload['chat_id'],-100123)
        self.assertEqual(payload['reply_parameters'],{'message_id':7})
        self.assertEqual(result.id,9)

    async def test_single_photo_upload_and_cleanup(self):
        bot=self.bot();paths=[]
        async def request(method,payload,files):
            self.assertEqual(method,'sendPhoto');self.assertEqual(payload['chat_id'],-100123)
            paths.extend(files.values())
            self.assertEqual(Path(paths[0]).read_bytes(),b'photo-bytes')
            return {'message_id':4}
        bot.request=request
        sent=await bot.send_file(-100123,['media'],caption='Deal')
        self.assertEqual(sent[0].id,4)
        self.assertFalse(paths[0].exists())

    async def test_album_upload_has_one_caption(self):
        bot=self.bot()
        async def request(method,payload,files):
            self.assertEqual(method,'sendMediaGroup')
            media=json.loads(payload['media'])
            self.assertEqual(len(media),2);self.assertEqual(media[0]['caption'],'Deal')
            self.assertNotIn('caption',media[1]);self.assertEqual(len(files),2)
            return [{'message_id':1},{'message_id':2}]
        bot.request=request
        self.assertEqual(len(await bot.send_file(-100123,['a','b'],caption='Deal')),2)

    async def test_large_image_falls_back_before_upload(self):
        bot=self.bot();bot.request=AsyncMock()
        with patch('bot_delivery.MAX_IMAGE_BYTES',3):
            with self.assertRaises(BotMediaUnavailable):
                await bot.send_file(-100123,['media'],caption='Deal')
        bot.request.assert_not_awaited()

    async def test_delivery_uses_bot_not_reader(self):
        reader=Obj(get_entity=AsyncMock(return_value=Obj(noforwards=True)),send_message=AsyncMock())
        bot=Obj(send_message=AsyncMock())
        await send_deal(reader,'Deal','https://t.me/example/1','B012345678',destination=-100123,sender=bot)
        reader.send_message.assert_not_awaited();bot.send_message.assert_awaited_once()

    async def test_send_failure_preserves_pending_product(self):
        db=database(':memory:');source='https://t.me/c/123/1'
        setstate(db,'channel_name:-100123','Source')
        record(db,'B012345678','Deal',source,1000,1,5)
        with patch('app.send_deal',new_callable=AsyncMock,side_effect=BotAPIError(403)):
            with self.assertRaises(BotAPIError):
                await send_pending(Obj(),db,{'destination':-100456},sender=Obj())
        self.assertEqual(db.execute('SELECT pending FROM deals').fetchone()[0],1)

    async def test_rate_limit_retry(self):
        http=HTTP([Response({'ok':False,'error_code':429,'parameters':{'retry_after':2}},429),
                   Response({'ok':True,'result':{'message_id':8}})])
        bot=BotDelivery(Reader(),http,'test-token')
        with patch('bot_delivery.asyncio.sleep',new_callable=AsyncMock) as sleep:
            await bot.send_message(-100123,'Deal')
            sleep.assert_awaited_once_with(2)
        self.assertEqual(len(http.calls),2)

    async def test_transport_exception_does_not_expose_token(self):
        http=Obj(post=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('https://api.telegram.org/botSECRET/sendMessage')))
        bot=BotDelivery(Reader(),http,'SECRET')
        with self.assertRaises(RuntimeError) as exc:
            await bot.send_message(-100123,'Deal')
        self.assertNotIn('SECRET',str(exc.exception));self.assertTrue(exc.exception.__suppress_context__)
