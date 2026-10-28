import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from app import log_config, log_product_sources, send_pending
from core import database, record, setstate, product_excerpt


class PostLoggingTests(unittest.IsolatedAsyncioTestCase):
    def test_startup_names_and_options(self):
        with self.assertLogs('deals', level='INFO') as logs:
            log_config({'channels': [-100123], 'timezone': 'Europe/Lisbon',
                        'digest_cron': '*/30 * * * *', 'api_hash': 'SECRET'},
                       {'-100123': 'Amazon Spain'})
        output = '\n'.join(logs.output)
        self.assertIn('-100123 (Amazon Spain)', output)
        self.assertIn('active_schedule', output)
        self.assertNotIn('SECRET', output)

    async def test_duplicate_counts_and_selected_cheapest_channel(self):
        db = database(':memory:')
        a, b = 'https://t.me/c/123/1', 'https://t.me/c/456/2'
        setstate(db, 'channel_name:-100123', 'Channel A')
        setstate(db, 'channel_name:-100456', 'Channel B')
        record(db, 'B012345678', 'first', a, 3000, 1, 5)
        reason = record(db, 'B012345678', 'cheaper', b, 2500, 2, 5)
        # Reprocessing an identical post must not inflate the count.
        record(db, 'B012345678', 'cheaper', b, 2500, 3, 5)
        with self.assertLogs('deals', level='INFO') as logs:
            await log_product_sources(SimpleNamespace(), db, 'B012345678', reason)
        output = '\n'.join(logs.output)
        self.assertIn('Duplicate posts found: 1', output)
        self.assertIn('-100123 (Channel A): 1 post(s)', output)
        self.assertIn('-100456 (Channel B): 1 post(s)', output)
        self.assertIn('Selected channel: -100456 (Channel B)', output)
        self.assertIn('lower advertised price', output)

    async def test_existing_pending_post_has_only_canonical_amazon_link(self):
        db = database(':memory:')
        source = 'https://t.me/c/123/1'
        setstate(db, 'channel_name:-100123', 'Channel A')
        text = 'Offer https://amazon.es/gp/product/B012345678?tag=affiliate https://amzn.eu/short'
        record(db, 'B012345678', text, source, 2500, 1, 5)
        with patch('app.send_deal', new_callable=AsyncMock) as send, patch('app.asyncio.sleep', new_callable=AsyncMock):
            await send_pending(SimpleNamespace(), db, {})
        body = send.call_args.args[1]
        self.assertIn('https://www.amazon.es/dp/B012345678', body)
        self.assertNotIn('tag=affiliate', body)
        self.assertNotIn('https://amzn.eu', body)
        self.assertIn('Channel: Channel A', body)
        self.assertEqual(db.execute('SELECT pending FROM deals').fetchone()[0], 0)

    def test_excerpt_preserves_other_links(self):
        self.assertIn('https://example.com/info', product_excerpt('Details https://example.com/info'))
