
import hashlib
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import img2pdf
import requests

from scrapers.image_quality import extract_candidates, choose_best_candidate


BASE = "https://m.samajaepaper.in"
EDCODE = 73
SUBCODE = 73

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def _page_url_variants(ds, n):
    keys = ("pgnum", "pageno", "page", "page_no", "pgno")
    return [
        f"{BASE}/indexnext.php?pagedate={ds}&edcode={EDCODE}"
        f"&subcode={SUBCODE}&mod=1&{key}={n}&type=a"
        for key in keys
    ]


def _resolve_page(session, html, page_url, page_no, seen):
    candidates = extract_candidates(
        html, page_url, page_no=page_no, reject_page_urls=False
    )

    # Samaja exposes the real page raster directly in the viewer HTML
    # (for example /epaperimages/.../06072026-md-an-1.jpg).
    # Do NOT return the first matching candidate: compare actual dimensions.
    return choose_best_candidate(
        session,
        candidates,
        page_url,
        seen_digests=seen,
        max_candidates=100,
        verbose=False,
    )


def download_samaja():
    d = datetime.now(ZoneInfo("Asia/Kolkata"))
    ds = d.strftime("%Y-%m-%d")
    out = Path(f"Samaja_{d:%Y%m%d}.pdf")
    files = []
    seen = set()
    session = requests.Session()

    print("=" * 60)
    print(f"📰 SAMAJA — BHUBANESWAR — {ds}")
    print("=" * 60)

    try:
        first_url = _page_url_variants(ds, 1)[0]
        first = session.get(first_url, headers=HEADERS, timeout=40)
        first.raise_for_status()

        nums = {
            int(x)
            for x in re.findall(r"Page\s*(?:No\.?)?\s*(\d+)", first.text, re.I)
            if 1 <= int(x) <= 100
        }
        nums.update(
            int(x)
            for x in re.findall(
                r"(?:pgnum|pageno|page|page_no)=(\d+)", first.text, re.I
            )
            if 1 <= int(x) <= 100
        )

        counts = re.findall(
            r'(?:totalPages|pageCount|total_page|totalPagesCount)'
            r'\s*[:=]\s*["\']?(\d+)',
            first.text,
            re.I,
        )
        if counts:
            nums.update(range(1, max(map(int, counts)) + 1))

        if not nums:
            raise RuntimeError("Samaja: no page numbers found")

        total = max(nums)
        print(f"🔎 Found {total} pages")

        for n in range(1, total + 1):
            selected = None

            for url in _page_url_variants(ds, n):
                try:
                    page = session.get(url, headers=HEADERS, timeout=40)
                    if not page.ok:
                        continue

                    candidate = _resolve_page(
                        session, page.text, page.url, n, seen
                    )

                    if candidate:
                        selected = candidate
                        break
                except requests.RequestException:
                    continue

            if not selected:
                raise RuntimeError(
                    f"Samaja: no high-quality image for page {n}"
                )

            seen.add(selected.digest)

            ext = "jpg" if selected.fmt.upper() in {"JPEG", "JPG"} else selected.fmt.lower()
            fn = Path(f"samaja_page_{n:02d}.{ext}")
            fn.write_bytes(selected.data)
            files.append(str(fn))

            print(
                f"✓ Page {n:02d} — {selected.width}x{selected.height} — "
                f"{len(selected.data)/1048576:.2f} MB — {selected.url}",
                flush=True,
            )

        # img2pdf embeds JPEGs without re-JPEG-compressing them.
        with out.open("wb") as f:
            f.write(img2pdf.convert(files))

        print(
            f"✅ Samaja PDF ready: {len(files)} pages / "
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
    download_samaja()
