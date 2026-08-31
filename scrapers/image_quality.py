
import hashlib
import io
import re
from dataclasses import dataclass
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from PIL import Image


MIN_IMAGE_BYTES = 50_000
MIN_WIDTH = 900
MIN_HEIGHT = 1200
MAX_RATIO = 1.35
MIN_RATIO = 0.40

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/jpeg,image/png,*/*;q=0.8",
}


@dataclass
class ImageCandidate:
    url: str
    hint_score: int = 0
    reason: str = ""


@dataclass
class ImageResult:
    data: bytes
    width: int
    height: int
    fmt: str
    url: str
    digest: str
    hint_score: int
    reason: str

    @property
    def pixels(self):
        return self.width * self.height


def normalize_url(raw, page_url):
    if not raw:
        return None
    raw = str(raw).strip().replace("\\/", "/").replace("&amp;", "&")
    raw = raw.replace("\\u0026", "&").replace("\\x26", "&")
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


def looks_like_image(data, content_type):
    return (
        content_type.startswith("image/")
        or data[:3] == b"\xff\xd8\xff"
        or data[:8] == b"\x89PNG\r\n\x1a\n"
        or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    )


def validate_image(data, content_type):
    if len(data) < MIN_IMAGE_BYTES or not looks_like_image(data, content_type):
        return None

    info = image_info(data)
    if not info:
        return None

    width, height, fmt = info
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        return None

    ratio = width / height
    if ratio < MIN_RATIO or ratio > MAX_RATIO:
        return None

    return width, height, fmt


def download_image(session, url, referer):
    response = session.get(
        url,
        headers={**HEADERS, "Referer": referer},
        timeout=(10, 60),
        allow_redirects=True,
    )
    if not response.ok:
        return None

    info = validate_image(
        response.content,
        response.headers.get("Content-Type", "").lower(),
    )
    if not info:
        return None

    width, height, fmt = info
    return ImageResult(
        data=response.content,
        width=width,
        height=height,
        fmt=fmt,
        url=response.url,
        digest=hashlib.sha256(response.content).hexdigest(),
        hint_score=0,
        reason="",
    )


def extract_candidates(html, page_url, page_no=None, reject_page_urls=True):
    soup = BeautifulSoup(html, "html.parser")
    found = {}

    def add(raw, score=0, reason=""):
        url = normalize_url(raw, page_url)
        if not url:
            return

        low = url.lower()
        path = low.split("?", 1)[0]

        if reject_page_urls and re.search(r"/page(?:/\d+)?/?$", path):
            return

        # Navigation/thumbnail assets are useful fallbacks but should lose
        # to a genuine full-page raster when both exist.
        bad = (
            "logo", "icon", "sprite", "favicon", "loader", "placeholder",
            "avatar", "share", "menu", "arrow", "close", "search",
            "fshared", "advert", "ads"
        )
        score -= 150 * sum(word in low for word in bad)

        if page_no is not None:
            if re.search(
                rf"(?:page|pg|pageno|page_no|pagenumber)[_-]?0*{page_no}(?:\D|$)",
                low,
                re.I,
            ):
                score += 250

            if re.search(
                rf"(?:^|[/_-])0*{page_no}(?:[-_.]|$)",
                low,
                re.I,
            ):
                score += 100

        # Explicitly demote thumbnail conventions.
        if re.search(r"[-_](?:s|sm|small|thumb|thumbnail)(?=\.(?:jpe?g|png|webp))", low):
            score -= 500

        if url not in found or score > found[url].hint_score:
            found[url] = ImageCandidate(url, score, reason)

    for tag in soup.find_all(["img", "source"]):
        attrs = tag.attrs
        try:
            w = int(attrs.get("width") or 0)
            h = int(attrs.get("height") or 0)
        except (TypeError, ValueError):
            w = h = 0

        area_hint = min((w * h) / 10000, 600) if w and h else 0

        for key in (
            "src", "data-src", "data-original", "data-image",
            "data-img", "data-url", "data-lazy-src",
            "data-filename", "data-image-url",
        ):
            if attrs.get(key):
                add(attrs[key], int(area_hint) + 150, key)

        if attrs.get("srcset"):
            for item in str(attrs["srcset"]).split(","):
                add(item.strip().split()[0], int(area_hint) + 120, "srcset")

    for tag in soup.find_all("meta"):
        prop = (tag.get("property") or tag.get("name") or "").lower()
        if prop in {"og:image", "twitter:image"}:
            add(tag.get("content"), 80, prop)

    scripts = "\n".join(
        s.string or s.get_text() or "" for s in soup.find_all("script")
    )

    for match in re.finditer(
        r'(?:(?:https?:)?//|/)[^"\'\\\s<>]+?\.(?:jpe?g|png|webp)'
        r'(?:\?[^"\'\\\s<>]*)?',
        scripts,
        re.I,
    ):
        add(match.group(0), 400, "script-raster")

    for match in re.finditer(r'["\']([^"\']{3,2000})["\']', scripts):
        raw = match.group(1)
        low = raw.lower()
        if any(k in low for k in (
            ".jpg", ".jpeg", ".png", ".webp",
            "image", "img", "epaper", "epaperimage",
            "imagedownload", "imageprocessor",
        )):
            add(raw, 300, "script-image")

    for tag in soup.find_all(True):
        for key, value in tag.attrs.items():
            if not (key.startswith("data-") or key.lower() in {"onclick", "href"}):
                continue
            if isinstance(value, list):
                value = " ".join(value)
            if not isinstance(value, str):
                continue
            for raw in re.findall(
                r'(?:https?:)?//[^"\'\s)]+|/[^"\'\s)]+',
                value,
            ):
                add(raw, 180, key)

    return sorted(
        found.values(),
        key=lambda c: c.hint_score,
        reverse=True,
    )


def choose_best_candidate(
    session,
    candidates,
    referer,
    seen_digests=None,
    max_candidates=100,
    verbose=False,
):
    seen_digests = seen_digests or set()
    valid = []

    for index, candidate in enumerate(candidates[:max_candidates], 1):
        try:
            result = download_image(session, candidate.url, referer)
        except requests.RequestException:
            continue

        if not result or result.digest in seen_digests:
            continue

        result.hint_score = candidate.hint_score
        result.reason = candidate.reason
        valid.append(result)

        if verbose:
            print(
                f"   candidate {index}: {result.width}x{result.height} "
                f"{result.fmt} {len(result.data)/1048576:.2f} MB "
                f"{candidate.reason} — {result.url}",
                flush=True,
            )

    if not valid:
        return None

    # QUALITY RULE:
    # Pixel area is the primary criterion. URL heuristics are only a tie-breaker.
    valid.sort(
        key=lambda r: (r.pixels, r.hint_score, len(r.data)),
        reverse=True,
    )
    return valid[0]
