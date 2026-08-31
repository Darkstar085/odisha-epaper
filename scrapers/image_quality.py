
import hashlib
import io
import re
from dataclasses import dataclass
from urllib.parse import urljoin

import requests
from PIL import Image

MIN_BYTES = 50_000
MIN_WIDTH = 900
MIN_HEIGHT = 1200
MIN_RATIO = 0.40
MAX_RATIO = 1.35

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/jpeg,image/png,*/*;q=0.8",
}

@dataclass
class ImageResult:
    data: bytes
    width: int
    height: int
    fmt: str
    url: str
    digest: str

def normalize_url(raw, page_url):
    if not raw:
        return None
    raw = str(raw).strip().replace("\\/", "/").replace("&amp;", "&")
    raw = raw.replace("\\u0026", "&")
    if raw.startswith(("data:", "javascript:", "mailto:", "tel:")):
        return None
    return urljoin(page_url, raw)

def image_info(data):
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            return im.width, im.height, im.format
    except Exception:
        return None

def validate_image(data, content_type=""):
    if len(data) < MIN_BYTES:
        return None
    info = image_info(data)
    if not info:
        return None
    w, h, fmt = info
    if w < MIN_WIDTH or h < MIN_HEIGHT:
        return None
    ratio = w / h
    if not MIN_RATIO <= ratio <= MAX_RATIO:
        return None
    return w, h, fmt

def download_image(session, url, referer=None):
    r = session.get(
        url,
        headers={**HEADERS, **({"Referer": referer} if referer else {})},
        timeout=(20, 90),
        allow_redirects=True,
    )
    if not r.ok:
        return None
    info = validate_image(r.content, r.headers.get("Content-Type", ""))
    if not info:
        return None
    w, h, fmt = info
    return ImageResult(
        r.content, w, h, fmt, r.url,
        hashlib.sha256(r.content).hexdigest()
    )

def extract_image_urls(html, page_url, page_no=None):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    urls = {}

    def add(raw, score=0):
        u = normalize_url(raw, page_url)
        if not u:
            return
        low = u.lower()
        if any(x in low for x in ("logo", "icon", "sprite", "favicon", "loader", "placeholder", "avatar")):
            score -= 1000
        if re.search(r"[-_](?:s|sm|small|thumb|thumbnail)(?=\.(?:jpe?g|png|webp)(?:\?|$))", low):
            score -= 1000
        if page_no is not None:
            if re.search(rf"(?:page|pg|pageno|page_no)[_-]?0*{page_no}(?:\D|$)", low, re.I):
                score += 500
            if re.search(rf"(?:^|[/_-])0*{page_no}(?:[-_.]|$)", low, re.I):
                score += 150
        urls[u] = max(score, urls.get(u, -10**9))

    for tag in soup.find_all(["img", "source"]):
        for key in ("src", "data-src", "data-original", "data-original-image",
                    "data-full", "data-full-image", "data-zoom-image",
                    "data-image", "data-img", "data-url", "data-image-url"):
            if tag.get(key):
                add(tag.get(key), 200)
        if tag.get("srcset"):
            for item in str(tag["srcset"]).split(","):
                add(item.strip().split()[0], 150)

    for tag in soup.find_all("meta"):
        prop = (tag.get("property") or tag.get("name") or "").lower()
        if prop in ("og:image", "twitter:image"):
            add(tag.get("content"), 100)

    for m in re.finditer(
        r'(?:(?:https?:)?//|/)[^"\'\\\s<>]+?\.(?:jpe?g|png|webp)(?:\?[^"\'\\\s<>]*)?',
        html, re.I
    ):
        add(m.group(0), 250)

    return sorted(urls, key=urls.get, reverse=True)


def extract_candidates(html, page_url, page_no=None, reject_page_urls=True):
    """Backward-compatible candidate extraction API used by existing scrapers."""
    from dataclasses import dataclass
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin
    import re

    @dataclass
    class Candidate:
        url: str
        hint_score: int = 0
        reason: str = ""

    soup = BeautifulSoup(html, "html.parser")
    found = {}

    def add(raw, score=0, reason=""):
        if not raw:
            return
        raw = str(raw).strip().replace("\\/", "/").replace("&amp;", "&")
        if raw.startswith(("data:", "javascript:", "mailto:", "tel:")):
            return
        u = urljoin(page_url, raw)
        low = u.lower()
        path = low.split("?", 1)[0]
        if reject_page_urls and re.search(r"/page(?:/\d+)?/?$", path):
            return
        if any(x in low for x in ("logo", "icon", "sprite", "favicon", "loader", "placeholder", "avatar")):
            score -= 1000
        if re.search(r"[-_](?:s|sm|small|thumb|thumbnail)(?=\.(?:jpe?g|png|webp)(?:\?|$))", low, re.I):
            score -= 1000
        if page_no is not None:
            if re.search(rf"(?:page|pg|pageno|page_no|pagenumber)[_-]?0*{page_no}(?:\D|$)", low, re.I):
                score += 500
            if re.search(rf"(?:^|[/_-])0*{page_no}(?:[-_.]|$)", low, re.I):
                score += 150
        old = found.get(u)
        if old is None or score > old.hint_score:
            found[u] = Candidate(u, score, reason)

    for tag in soup.find_all(["img", "source"]):
        for key in (
            "src", "data-src", "data-original", "data-original-image",
            "data-full", "data-full-image", "data-zoom-image",
            "data-image", "data-img", "data-url", "data-image-url"
        ):
            if tag.get(key):
                add(tag.get(key), 200, key)
        if tag.get("srcset"):
            for item in str(tag["srcset"]).split(","):
                add(item.strip().split()[0], 150, "srcset")

    for tag in soup.find_all("meta"):
        prop = (tag.get("property") or tag.get("name") or "").lower()
        if prop in ("og:image", "twitter:image"):
            add(tag.get("content"), 100, prop)

    for m in re.finditer(
        r'(?:(?:https?:)?//|/)[^"\'\\\s<>]+?\.(?:jpe?g|png|webp)(?:\?[^"\'\\\s<>]*)?',
        html, re.I
    ):
        add(m.group(0), 250, "raster")

    return sorted(found.values(), key=lambda c: c.hint_score, reverse=True)


def choose_best_candidate(session, candidates, referer=None,
                          seen_digests=None, max_candidates=100, verbose=False):
    """Backward-compatible resolver: choose by actual pixel area, then hint score."""
    seen_digests = seen_digests or set()
    valid = []
    for i, candidate in enumerate(candidates[:max_candidates], 1):
        try:
            result = download_image(session, candidate.url, referer)
        except Exception:
            continue
        if not result or result.digest in seen_digests:
            continue
        if hasattr(candidate, "hint_score"):
            result.hint_score = candidate.hint_score
        valid.append(result)
        if verbose:
            print(
                f"   candidate {i}: {result.width}x{result.height} "
                f"{len(result.data)/1048576:.2f} MB — {result.url}",
                flush=True,
            )
    if not valid:
        return None
    valid.sort(
        key=lambda x: (x.width * x.height, getattr(x, "hint_score", 0), len(x.data)),
        reverse=True,
    )
    return valid[0]
