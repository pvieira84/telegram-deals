import sys
import unittest
from types import SimpleNamespace as Obj, ModuleType
from unittest.mock import AsyncMock, patch
from app import poll
from core import database, getstate, setstate

class HiddenLink:
    def __init__(self,url): self.url=url

class Client:
    def __init__(self,messages,failed=None):
        self.messages=messages
        self.failed=failed
        self.visited=[]
    async def get_entity(self,channel):
        self.visited.append(channel)
        if channel==self.failed: raise PermissionError('no access')
        return Obj(id=abs(channel)-100000,chatid=channel,title=f'Channel {channel}',username=None)
    async def get_messages(self,entity,limit):
        return [Obj(id=100)]
    async def iter_messages(self,entity,min_id,reverse):
        for m in self.messages.get(entity.chatid,[]):
            if m.id>min_id: yield m

def msg(i,text='',entities=None,buttons=None):
    return Obj(id=i,raw_text=text,entities=entities or [],buttons=buttons or [])

class ChannelScanTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        telethon=ModuleType('telethon');telethon.utils=Obj(get_peer_id=lambda e:e.chatid)
        types=ModuleType('telethon.tl.types');types.MessageEntityTextUrl=HiddenLink
        self.modules=patch.dict(sys.modules,{'telethon':telethon,'telethon.tl':ModuleType('telethon.tl'),'telethon.tl.types':types})
        self.modules.start();self.addCleanup(self.modules.stop)
        self.db=database(':memory:')
        self.cfg={'channels':[-100123,-100456,-100789],'price_drop_percent':5}
        for channel in self.cfg['channels']: setstate(self.db,f'cursor:{channel}',0)

    async def test_all_channels_scanned_including_quiet_and_unrecognised(self):
        c=Client({-100123:[msg(1,'Offer https://www.amazon.es/dp/B012345678')],
                  -100456:[msg(2,'See https://example.com/deal?secret=hidden')]})
        with self.assertLogs('deals',level='INFO') as logs:
            await poll(c,self.db,self.cfg,None)
        self.assertEqual(c.visited,self.cfg['channels'])
        out='\n'.join(logs.output)
        self.assertIn('3/3 successful, 0 failed',out)
        self.assertIn('Channel -100789 (Channel -100789) | Messages scanned: 0',out)
        self.assertIn('No recognised product: 1',out)
        self.assertIn('example.com: 1',out)
        self.assertNotIn('secret=hidden',out)
        self.assertEqual(getstate(self.db,'cursor:-100456'),'2')

    async def test_failure_does_not_starve_later_channels(self):
        c=Client({-100789:[msg(1,'https://amazon.es/dp/B012345678')]},failed=-100456)
        with self.assertLogs('deals',level='INFO') as logs:
            await poll(c,self.db,self.cfg,None)
        self.assertEqual(c.visited,self.cfg['channels'])
        self.assertIn('2/3 successful, 1 failed','\n'.join(logs.output))
        self.assertEqual(getstate(self.db,'cursor:-100456'),'0')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM deals').fetchone()[0],1)

    async def test_hidden_and_button_links_from_different_channels_merge(self):
        c=Client({-100123:[msg(1,'Offer',entities=[HiddenLink('https://amazon.es/dp/B012345678')])],
                  -100456:[msg(2,'Offer',buttons=[[Obj(url='https://amazon.es/dp/B012345678')]])]})
        with self.assertLogs('deals',level='INFO') as logs:
            await poll(c,self.db,self.cfg,None)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM deals').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM origins').fetchone()[0],2)
        self.assertIn('Duplicate posts found: 1','\n'.join(logs.output))

    async def test_first_run_initialises_without_importing_history(self):
        self.db.execute('DELETE FROM state')
        c=Client({})
        with self.assertLogs('deals',level='INFO') as logs:
            await poll(c,self.db,self.cfg,None)
        self.assertEqual(c.visited,self.cfg['channels'])
        self.assertEqual(getstate(self.db,'cursor:-100789'),'100')
        self.assertIn('collecting new posts from now','\n'.join(logs.output))
