
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
    category = _fetch_page(
        session,
        f"{BASE}/category/7/bhubaneswar",
    )

    soup = BeautifulSoup(category.content, "html.parser")

    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = " ".join(a.stripped_strings).lower()
        if "twin-city" in href.lower() and date in href and "/edition/" in href:
            return urljoin(category.url, href)

    for raw in re.findall(
        r'href=["\']([^"\']*/edition/\d+/[^"\']*twin-city[^"\']*)["\']',
        category.text,
        re.I,
    ):
        if date in raw:
            return urljoin(category.url, raw)

    raise RuntimeError(
        f"Pragativadi: today's TWIN CITY edition not found for {date}"
    )


def _page_variants(edition, page_no):
    base = edition.rstrip("/")
    return [
        f"{base}/page/{page_no}",
        f"{base}/page/{page_no}/",
    ]


def _find_total_pages(html):
    nums = set()

    for text in BeautifulSoup(html, "html.parser").stripped_strings:
        m = re.fullmatch(r"Page No\s+(\d{1,3})", text)
        if m:
            nums.add(int(m.group(1)))

    if not nums:
        for text in BeautifulSoup(html, "html.parser").stripped_strings:
            for m in re.finditer(r"TWIN CITY.*?-(\d{1,3})$", text, re.I):
                nums.add(int(m.group(1)))

    if not nums:
        return 0

    total = max(nums)
    if sorted(nums) != list(range(1, total + 1)):
        raise RuntimeError(
            f"Pragativadi: incomplete page sequence {sorted(nums)}"
        )
    return total


def _resolve_page(session, edition, page_no, seen):
    last_error = None

    for page_url in _page_variants(edition, page_no):
        try:
            response = _fetch_page(session, page_url)

            candidates = extract_candidates(
                response.text,
                response.url,
                page_no=page_no,
                reject_page_urls=True,
            )

            # The previous implementation returned the FIRST valid image.
            # That is the main quality bug: a thumbnail/preview can win even
            # when the viewer also exposes a much larger original.
            selected = choose_best_candidate(
                session,
                candidates,
                response.url,
                seen_digests=seen,
                max_candidates=100,
                verbose=False,
            )

            if selected:
                return selected

        except requests.RequestException as exc:
            last_error = exc

    if last_error:
        print(
            f"   ⚠ page {page_no}: {type(last_error).__name__}: {last_error}",
            flush=True,
        )

    return None


def download_pragativadi():
    d = datetime.now(ZoneInfo("Asia/Kolkata"))
    date = d.strftime("%d-%m-%Y")
    out = Path(f"Pragativadi_{d:%Y%m%d}.pdf")

    files = []
    seen = set()
    session = requests.Session()

    print("=" * 60)
    print(f"📰 PRAGATIVADI — TWIN CITY — {date}")
    print("=" * 60)

    try:
        edition = _find_edition(session, date)
        print(f"✓ Edition: {edition}")

        edition_response = _fetch_page(session, edition)
        total = _find_total_pages(edition_response.text)

        if not total:
            raise RuntimeError("Pragativadi: no page numbers found")

        print(f"🔎 Found {total} pages")

        for page_no in range(1, total + 1):
            print(
                f"📄 Page {page_no}/{total} — resolving highest-resolution raster",
                flush=True,
            )

            selected = _resolve_page(
                session,
                edition,
                page_no,
                seen,
            )

            if not selected:
                raise RuntimeError(
                    f"Pragativadi: no high-quality image for page {page_no}"
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

        # Removed the old JPEG quality=92 / 4:2:0 recompression.
        # img2pdf embeds JPEG page rasters directly.
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
