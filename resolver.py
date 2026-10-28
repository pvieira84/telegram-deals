"""Bounded URL/landing-page resolution for supported deal sources."""
import logging
from html.parser import HTMLParser
from urllib.parse import urlsplit, urljoin

from core import product, embedded_asins

LOG = logging.getLogger('deals')
LANDING_HOSTS = {'cupoestafixe.com', 'www.cupoestafixe.com'}
SHORT_HOSTS = {'amzn.eu', 'amzn.to', 'link.amazon', 'amzlinks.in', 'www.amzlinks.in'}
RETAIL_HOSTS = {'amazon.es', 'www.amazon.es'}
FETCH_HOSTS = LANDING_HOSTS | SHORT_HOSTS | RETAIL_HOSTS
MAX_HTML = 1024 * 1024


def fetchable(url):
    try:
        u = urlsplit(url)
        return (u.scheme == 'https' and u.port in (None,443) and not u.username and
                not u.password and (u.hostname or '').lower() in FETCH_HOSTS)
    except ValueError:
        return False


class ProductLinks(HTMLParser):
    """Prefer article content, excluding navigation and recommendation sidebars."""
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.stack = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        parent_score = self.stack[-1][1] if self.stack else 0
        skipped = (self.stack[-1][2] if self.stack else False) or tag in {'nav','aside','footer','header','script','style'}
        classes = attrs.get('class','').split()
        score = max(parent_score, 3 if any(c in classes for c in ['entry-content','post-content','article-content']) else
                    2 if tag=='article' else 1 if tag=='main' else 0)
        if tag == 'a' and not skipped:
            url = urljoin(self.base,attrs.get('href',''))
            try:
                host = (urlsplit(url).hostname or '').lower()
            except ValueError:
                host = ''
            if url.startswith('https://') and (host in SHORT_HOSTS or product(url) or embedded_asins(url)):
                self.links.append((score,url))
        if tag not in {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}:
            self.stack.append((tag,score,skipped))

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def candidates(self):
        if not self.links:
            return []
        score = max(item[0] for item in self.links)
        return sorted({url for rank,url in self.links if rank==score})


async def resolve_all(url, http, *, allow_landing=True, visited=None):
    visited = set() if visited is None else visited
    current = url
    for _ in range(8):
        if current in visited:
            return set()
        visited.add(current)
        try:
            host = (urlsplit(current).hostname or '').lower()
        except ValueError:
            return set()
        # Shortener paths are opaque tokens, even when they resemble an ASIN.
        if host not in SHORT_HOSTS:
            direct = product(current)
            found = {direct} if direct else embedded_asins(current)
            if found:
                return found
        if not fetchable(current):
            return set()
        try:
            async with http.get(current,allow_redirects=False) as response:
                if response.status in (301,302,303,307,308):
                    location = response.headers.get('Location')
                    if not location:
                        return set()
                    current = urljoin(current,location)
                    continue
                if not allow_landing or host not in LANDING_HOSTS:
                    return set()
                if response.status != 200:
                    LOG.warning('Landing page %s returned HTTP %s.',host,response.status)
                    return set()
                if 'html' not in response.headers.get('Content-Type','').lower():
                    LOG.warning('Landing page %s did not return HTML.',host)
                    return set()
                data = bytearray()
                while len(data) <= MAX_HTML:
                    chunk = await response.content.read(min(65536, MAX_HTML+1-len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                if len(data)>MAX_HTML:
                    LOG.warning('Landing page %s exceeded the HTML size limit.',host)
                    return set()
                parser = ProductLinks(current)
                parser.feed(data.decode(response.charset or 'utf-8',errors='replace'))
            candidates = parser.candidates()
            if len(candidates)>8:
                LOG.warning('Landing page %s has too many candidate links; skipped.',host)
                return set()
            found = set()
            for candidate in candidates:
                found.update(await resolve_all(candidate,http,allow_landing=False,visited=visited))
            if len(found)==1:
                LOG.info('Landing page %s resolved to ASIN %s.',host,next(iter(found)))
                return found
            LOG.warning('Landing page %s resolved to %s distinct products; skipped as %s.',
                        host,len(found),'ambiguous' if found else 'unresolved')
            return set()
        except Exception as exc:
            LOG.warning('Link resolution failed for %s (%s).',host,type(exc).__name__)
            return set()
    LOG.warning('Link resolution exceeded the redirect limit.')
    return set()
