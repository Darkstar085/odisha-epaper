
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


BASE = "https://sambadepaper.com"
INDEX = f"{BASE}/indexnext.php"
MAX_PAGES = 100

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def _find_bhubaneswar_edition(session, date_iso):
    response = session.get(INDEX, headers=HEADERS, timeout=40)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    wanted = date_iso

    for a in soup.find_all("a", href=True):
        text = " ".join(a.stripped_strings).upper()
        href = a["href"]

        if "BHUBANESWAR" not in text:
            continue

        match = re.search(
            r"/epaper/1/(\d+)/(\d{4}-\d{2}-\d{2})/1",
            href,
            re.I,
        )
        if match and match.group(2) == wanted:
            return urljoin(response.url, href)

    # Fallback: search the raw HTML for a matching edition URL near
    # BHUBANESWAR. This handles the site's slightly inconsistent markup.
    for match in re.finditer(
        r'href=["\']([^"\']*/epaper/1/\d+/' + re.escape(wanted) + r'/1)["\']',
        response.text,
        re.I,
    ):
        href = urljoin(response.url, match.group(1))
        return href

    raise RuntimeError(
        f"Sambad: today's BHUBANESWAR edition not found for {date_iso}"
    )


def _page_url(edition_url, page_no):
    return re.sub(r"/1$", f"/{page_no}", edition_url)


def _find_total_pages(html):
    numbers = {
        int(x)
        for x in re.findall(r"Page\s*(?:No\.?)?\s*(\d+)", html, re.I)
        if 1 <= int(x) <= MAX_PAGES
    }
    return max(numbers) if numbers else 0


def download_sambad():
    d = datetime.now(ZoneInfo("Asia/Kolkata"))
    date_iso = d.strftime("%Y-%m-%d")
    date_compact = d.strftime("%d%m%Y")
    out = Path(f"Sambad_bhubaneswar_{date_compact}.pdf")

    files = []
    seen = set()
    session = requests.Session()

    print("=" * 60)
    print(f"📰 SAMBAD — BHUBANESWAR — {date_iso}")
    print("=" * 60)

    try:
        edition = _find_bhubaneswar_edition(session, date_iso)
        print(f"✓ Edition: {edition}")

        first = session.get(
            _page_url(edition, 1),
            headers=HEADERS,
            timeout=40,
        )
        first.raise_for_status()

        total = _find_total_pages(first.text)
        if not total:
            raise RuntimeError("Sambad: no page numbers found")

        print(f"🔎 Found {total} pages")

        for page_no in range(1, total + 1):
            page_url = _page_url(edition, page_no)
            print(f"📄 Page {page_no}/{total} — resolving images", flush=True)

            response = session.get(page_url, headers=HEADERS, timeout=40)
            response.raise_for_status()

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
                seen_digests=seen,
                max_candidates=100,
                verbose=False,
            )

            if not selected:
                raise RuntimeError(
                    f"Sambad: no high-quality image for page {page_no}"
                )

            seen.add(selected.digest)

            ext = "jpg" if selected.fmt.upper() in {"JPEG", "JPG"} else selected.fmt.lower()
            fn = Path(f"sambad_{page_no:02d}.{ext}")
            fn.write_bytes(selected.data)
            files.append(str(fn))

            print(
                f"✓ Page {page_no:02d} — {selected.width}x{selected.height} — "
                f"{len(selected.data)/1048576:.2f} MB — {selected.url}",
                flush=True,
            )

        # IMPORTANT: do not use the old ...-md-hr-{page}.jpg guess.
        # The live viewer exposes the actual page raster, including
        # edition-specific names such as ...-md-hr-1.jpg or ...-md-bl-1.jpg.
        with out.open("wb") as pdf:
            pdf.write(img2pdf.convert(files))

        print(
            f"✅ Sambad PDF ready: {len(files)} pages / "
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
    download_sambad()
