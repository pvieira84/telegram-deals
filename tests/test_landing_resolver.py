import unittest
from types import SimpleNamespace as Obj
from core import embedded_asins
from resolver import resolve_all, ProductLinks

class Response:
    def __init__(self,status=200,html='',location=None,chunk_size=65536):
        self.status=status
        self.headers={'Content-Type':'text/html'}
        if location: self.headers['Location']=location
        self.charset='utf-8';self.data=html.encode();self.chunk_size=chunk_size
        self.content=self
    async def read(self,n):
        n=min(n,self.chunk_size);chunk,self.data=self.data[:n],self.data[n:];return chunk
    async def __aenter__(self): return self
    async def __aexit__(self,*args): pass

class HTTP:
    def __init__(self,responses): self.responses=responses;self.calls=[]
    def get(self,url,allow_redirects):
        self.calls.append(url)
        if url not in self.responses: raise AssertionError(f'Unexpected fetch: {url}')
        return self.responses[url]

PAGE='https://www.cupoestafixe.com/?p=291538'
SHORT='https://link.amazon/B0hZZPMr6'

class LandingResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_to_shortener_to_canonical_product(self):
        http=HTTP({PAGE:Response(html=f'<article><div class="entry-content"><a href="{SHORT}">Comprar</a></div></article>',chunk_size=7),
                   SHORT:Response(302,location='https://www.amazon.es/dp/B0CFVYPKGB?tag=affiliate')})
        self.assertEqual(await resolve_all(PAGE,http),{'B0CFVYPKGB'})
        self.assertEqual(http.calls,[PAGE,SHORT])

    async def test_token_that_looks_like_asin_is_resolved(self):
        short='https://link.amazon/B012345678'
        http=HTTP({short:Response(302,location='https://amazon.es/dp/B0CFVYPKGB')})
        self.assertEqual(embedded_asins(short),set())
        self.assertEqual(await resolve_all(short,http),{'B0CFVYPKGB'})

    async def test_ambiguous_page_is_not_misattributed(self):
        html='<article><a href="https://amazon.es/dp/B0CFVYPKGB">One</a><a href="https://amazon.es/dp/B0DYH2XRPB">Two</a></article>'
        with self.assertLogs('deals',level='WARNING'):
            self.assertEqual(await resolve_all(PAGE,HTTP({PAGE:Response(html=html)})),set())

    async def test_article_content_preferred_to_sidebar(self):
        html='<aside><a href="https://amazon.es/dp/B0DYH2XRPB">Other</a></aside><article><div class="entry-content"><a href="https://amazon.es/dp/B0CFVYPKGB">Deal</a></div></article>'
        self.assertEqual(await resolve_all(PAGE,HTTP({PAGE:Response(html=html)})),{'B0CFVYPKGB'})

    async def test_redirect_to_unapproved_host_not_fetched(self):
        http=HTTP({SHORT:Response(302,location='https://unapproved.example/landing')})
        self.assertEqual(await resolve_all(SHORT,http),set())
        self.assertEqual(http.calls,[SHORT])

    async def test_loop_stops(self):
        http=HTTP({SHORT:Response(302,location=SHORT)})
        self.assertEqual(await resolve_all(SHORT,http),set())
        self.assertEqual(http.calls,[SHORT])

    async def test_protected_page_stays_unresolved(self):
        with self.assertLogs('deals',level='WARNING'):
            self.assertEqual(await resolve_all(PAGE,HTTP({PAGE:Response(403)})),set())

    async def test_no_arbitrary_website_fetching(self):
        http=HTTP({})
        self.assertEqual(await resolve_all('https://other.example/?p=291538',http),set())
        self.assertEqual(http.calls,[])

    async def test_oversized_page_is_rejected(self):
        with self.assertLogs('deals',level='WARNING'):
            self.assertEqual(await resolve_all(PAGE,HTTP({PAGE:Response(html='x'*(1024*1024+1))})),set())
