import concurrent.futures
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import img2pdf
import requests
from bs4 import BeautifulSoup

from scrapers.image_quality import extract_candidates, choose_best_candidate

BASE = "https://epaper.pragativadi.com"
MAX_WORKERS = 6

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def _fetch_page(session, url):
    response = session.get(
        url,
        headers={**HEADERS, "Cache-Control": "no-cache", "Pragma": "no-cache"},
        timeout=(10, 45),
    )
    response.raise_for_status()
    return response


def _find_edition(session, date):
    direct_urls = (
        f"{BASE}/edition/twin-city/{date}/page/1",
        f"{BASE}/edition/twin-city/{date}",
    )

    for url in direct_urls:
        try:
            response = _fetch_page(session, url)
        except requests.RequestException:
            continue
        low = response.text.lower()
        if date in low and "twin city" in low:
            return response.url

    legacy = _fetch_page(session, f"{BASE}/epaper?alias=bhubaneswar&id=7")
    soup = BeautifulSoup(legacy.content, "html.parser")

    for a in soup.find_all("a", href=True):
        href = urljoin(legacy.url, a["href"])
        if "/edition/" in href and date in href and "twin-city" in href.lower():
            return href

    raise RuntimeError(f"Pragativadi: today's TWIN CITY edition not found for {date}")


def _page_variants(edition, page_no):
    base = edition.rstrip("/")
    base = re.sub(r"/page/\d+$", "", base)
    return [
        f"{base}/page/{page_no}",
        f"{base}/page/{page_no}/",
    ]


def _find_total_pages(html):
    nums = set()

    for match in re.finditer(r"(?:Page\s*(?:No\.?)?\s*|pageno=)(\d+)", html, re.I):
        nums.add(int(match.group(1)))

    for match in re.finditer(r"/page/(\d+)", html, re.I):
        nums.add(int(match.group(1)))

    if not nums:
        for text in BeautifulSoup(html, "html.parser").stripped_strings:
            m = re.fullmatch(r"Page\s+No\s+(\d{1,3})", text)
            if m:
                nums.add(int(m.group(1)))

    return max(nums) if nums else 0


def _resolve_page(edition, page_no):
    session = requests.Session()

    for page_url in _page_variants(edition, page_no):
        try:
            response = _fetch_page(session, page_url)
            candidates = extract_candidates(
                response.text,
                response.url,
                page_no=page_no,
                reject_page_urls=True,
            )

            selected = choose_best_candidate(
                session,
                candidates,
                response.url,
                seen_digests=set(),
                max_candidates=100,
                verbose=False,
            )

            if selected:
                return page_no, selected
        except requests.RequestException:
            continue

    return page_no, None


def download_pragativadi():
    d = datetime.now(ZoneInfo("Asia/Kolkata"))
    date_iso = d.strftime("%Y-%m-%d")
    date = d.strftime("%d-%m-%Y")
    out = Path(f"Pragativadi_{d:%Y%m%d}.pdf")

    files = []

    print("=" * 60)
    print(f"📰 PRAGATIVADI — TWIN CITY — {date}")
    print("=" * 60)

    try:
        session = requests.Session()
        edition = _find_edition(session, date_iso)
        print(f"✓ Edition: {edition}")

        edition_response = _fetch_page(session, edition)
        total = _find_total_pages(edition_response.text)

        if not total:
            raise RuntimeError("Pragativadi: no page numbers found")

        print(f"🔎 Found {total} pages")
        print(f"⚡ Resolving up to {MAX_WORKERS} pages concurrently...")

        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            results = list(pool.map(lambda n: _resolve_page(edition, n), range(1, total + 1)))

        results.sort(key=lambda item: item[0])
        seen = set()

        for page_no, selected in results:
            if not selected:
                raise RuntimeError(
                    f"Pragativadi: no high-quality image for page {page_no}"
                )

            if selected.digest in seen:
                raise RuntimeError(
                    f"Pragativadi: duplicate image detected on page {page_no}: "
                    f"{selected.url}"
                )
            seen.add(selected.digest)

            ext = "jpg" if selected.fmt.upper() in {"JPEG", "JPG"} else selected.fmt.lower()
            fn = Path(f"pragativadi_page_{page_no:02d}.{ext}")
            fn.write_bytes(selected.data)
            files.append(str(fn))

            print(
                f"✓ Page {page_no:02d} — {selected.width}x{selected.height} — "
                f"{len(selected.data)/1048576:.2f} MB — {selected.url}",
                flush=True,
            )

        with out.open("wb") as pdf:
            pdf.write(img2pdf.convert(files))

        print(
            f"✅ Pragativadi PDF ready: {len(files)} pages / "
            f"{out.stat().st_size/1048576:.2f} MB"
        )
        return str(out)

    finally:
        for filename in files:
            try:
                os.remove(filename)
            except OSError:
                pass


if __name__ == "__main__":
    download_pragativadi()
