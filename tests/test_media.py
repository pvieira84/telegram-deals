import unittest
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock
from media import original_images, send_deal, source_reference


def photo(i, group=None):
    return Obj(id=i,grouped_id=group,photo=True,document=None,media=f'photo-{i}')


def client():
    return Obj(get_entity=AsyncMock(return_value=Obj(noforwards=False)),
               get_messages=AsyncMock(return_value=photo(5)),
               send_file=AsyncMock(return_value=[Obj(id=99)]),send_message=AsyncMock())


class MediaTests(unittest.IsolatedAsyncioTestCase):
    def test_source_reference(self):
        self.assertEqual(source_reference('https://t.me/c/1204717179/5'),(-1001204717179,5))
        self.assertEqual(source_reference('https://t.me/deals/5'),('deals',5))
        self.assertIsNone(source_reference('https://other.test/deals/5'))

    async def test_photo_caption(self):
        c=client()
        await send_deal(c,'deal text','https://t.me/deals/5','B012345678')
        c.send_file.assert_awaited_once_with('me',['photo-5'],caption='deal text',parse_mode=None)
        c.send_message.assert_not_awaited()

    async def test_album_excludes_other_posts(self):
        c=client()
        c.get_messages.side_effect=[photo(5,77),[photo(6,77),photo(4,66),photo(5,77),None]]
        self.assertEqual(await original_images(c,'https://t.me/deals/5'),['photo-5','photo-6'])

    async def test_protected_source(self):
        c=client();c.get_entity.return_value=Obj(noforwards=True)
        await send_deal(c,'text','https://t.me/deals/5','B012345678')
        c.send_file.assert_not_awaited();c.send_message.assert_awaited_once()

    async def test_deleted_source(self):
        c=client();c.get_messages.return_value=None
        await send_deal(c,'text','https://t.me/deals/5','B012345678')
        c.send_file.assert_not_awaited();c.send_message.assert_awaited_once()

    async def test_long_caption_uses_reply(self):
        c=client();body='😀'*600
        await send_deal(c,body,'https://t.me/deals/5','B012345678')
        self.assertLess(len(c.send_file.call_args.kwargs['caption']),1024)
        self.assertEqual(c.send_message.call_args.kwargs['reply_to'],99)

    async def test_send_failure_keeps_pending_via_exception(self):
        c=client();c.send_file.side_effect=ConnectionError('offline')
        with self.assertRaises(ConnectionError):
            await send_deal(c,'text','https://t.me/deals/5','B012345678')
        c.send_message.assert_not_awaited()
