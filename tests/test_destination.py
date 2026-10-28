import unittest
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock
from app import destination_value, validate_destination
from media import send_deal

class DestinationTests(unittest.IsolatedAsyncioTestCase):
    def test_defaults_and_validation(self):
        self.assertEqual(destination_value({}),'me')
        self.assertEqual(destination_value({'destination':'-1001234567890'}),-1001234567890)
        for value in [None,True,123,'', 'someone', '@public']:
            with self.assertRaises(ValueError): destination_value({'destination':value})
        with self.assertRaises(ValueError):
            destination_value({'destination':-1001234567890,'channels':[-1001234567890]})

    async def test_owner_can_post_and_nonadmin_cannot(self):
        c=Obj(get_entity=AsyncMock(return_value=Obj(broadcast=True,creator=True,title='My deals')))
        self.assertEqual(await validate_destination(c,{'destination':-100123}),-100123)
        c.get_entity.return_value=Obj(broadcast=True,creator=False,admin_rights=None)
        with self.assertRaises(ValueError): await validate_destination(c,{'destination':-100123})
        c.get_entity.return_value=Obj(broadcast=True,creator=False,admin_rights=Obj(post_messages=True))
        self.assertEqual(await validate_destination(c,{'destination':-100123}),-100123)

    async def test_photo_and_long_text_reply_use_same_destination(self):
        c=Obj(get_entity=AsyncMock(return_value=Obj(noforwards=False)),
              get_messages=AsyncMock(return_value=Obj(photo=True,media='photo',grouped_id=None)),
              send_file=AsyncMock(return_value=[Obj(id=9)]),send_message=AsyncMock())
        await send_deal(c,'x'*1200,'https://t.me/example/1','B012345678',destination=-100123)
        self.assertEqual(c.send_file.call_args.args[0],-100123)
        self.assertEqual(c.send_message.call_args.args[0],-100123)
        self.assertEqual(c.send_message.call_args.kwargs['reply_to'],9)

    async def test_text_fallback_uses_destination(self):
        c=Obj(get_entity=AsyncMock(return_value=Obj(noforwards=True)),send_message=AsyncMock())
        await send_deal(c,'text','https://t.me/example/1','B012345678',destination=-100123)
        self.assertEqual(c.send_message.call_args.args[0],-100123)
