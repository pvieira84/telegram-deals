import unittest
from unittest.mock import Mock
from core import embedded_asins, product_excerpt
from app import resolve

class EmbeddedAsinTests(unittest.IsolatedAsyncioTestCase):
    def test_external_links_and_encoding(self):
        cases = [
            'https://boacena.pt/B0CFVYPKGB',
            'https://descontosainanas.com/oferta-b0cfvypkgb/',
            'https://example.com/go?product=B0CFVYPKGB&tag=affiliate',
            'https://example.com/go?url=https%253A%252F%252Famazon.es%252Fdp%252FB0CFVYPKGB',
            'https://example.com/#B0CFVYPKGB',
        ]
        for url in cases:
            with self.subTest(url=url):
                self.assertEqual(embedded_asins(url),{'B0CFVYPKGB'})

    def test_explicit_older_asin(self):
        self.assertEqual(embedded_asins('https://example.com/?asin=123456789X'),{'123456789X'})
        self.assertEqual(embedded_asins('https://example.com/dp/B123456789'),{'B123456789'})

    def test_multiple_products(self):
        self.assertEqual(embedded_asins('https://example.com/?a=B0CFVYPKGB&b=B0DYH2XRPB'),{'B0CFVYPKGB','B0DYH2XRPB'})

    def test_reject_unrelated_ids_and_hostname(self):
        for url in ['https://example.com/?id=1234567890','https://example.com/ABCDEFGHIJ',
                    'https://example.com/XXB0CFVYPKGBYY','https://B0CFVYPKGB.example.com/',
                    'https://B0CFVYPKGB:password@example.com/', 'not-a-url-B0CFVYPKGB']:
            self.assertEqual(embedded_asins(url),set())

    async def test_external_asin_requires_no_http(self):
        http=Mock()
        self.assertEqual(await resolve('https://boacena.pt/B0CFVYPKGB',http),'B0CFVYPKGB')
        http.get.assert_not_called()

    def test_external_tracking_link_removed_from_outgoing_excerpt(self):
        text=product_excerpt('Offer https://boacena.pt/B0CFVYPKGB?tag=affiliate')
        self.assertNotIn('boacena.pt',text)
        self.assertIn('[Amazon link below]',text)
