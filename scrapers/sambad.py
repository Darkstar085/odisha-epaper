
import hashlib
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urljoin

import img2pdf
import requests
from bs4 import BeautifulSoup

from .image_quality import HEADERS, download_image

BASE = "https://sambadepaper.com"
MOBILE = "https://m.sambadepaper.com"

def get(session, url):
    r = session.get(url, headers=HEADERS, timeout=(20, 60))
    r.raise_for_status()
    return r

def find_edition(session, date_iso):
    """Find the current Bhubaneswar Sambad viewer.

    The public edition number is not stable and the homepage/index can lag
    behind the current date. Prefer explicit archive links, then probe the
    compact edition-id range and identify the page whose title names
    BHUBANESWAR.
    """
    import concurrent.futures

    index_urls = (
        f"{MOBILE}/indexnext.php",
        f"{BASE}/indexnext.php",
        "https://pdf.sambadepaper.com/",
    )

    for index in index_urls:
        try:
            r = get(session, index)
        except requests.RequestException:
            continue

        # Look for a direct current-date edition URL in any form.
        patterns = [
            rf'["\']([^"\']*/epaper/1/(\d+)/{re.escape(date_iso)}/1)["\']',
            rf'["\']([^"\']*/epaper/1/(\d+)/{re.escape(date_iso)}/[^"\']*)["\']',
        ]
        for pat in patterns:
            for m in re.finditer(pat, r.text, re.I):
                u = urljoin(r.url, m.group(1))
                try:
                    vr = get(session, u)
                except requests.RequestException:
                    continue
                low = vr.text.lower()
                if "bhubaneswar" in low and date_iso in low:
                    return u

    def probe(edition_id):
        u = f"{MOBILE}/epaper/1/{edition_id}/{date_iso}/1"
        try:
            r = session.get(u, headers=HEADERS, timeout=(10, 25), allow_redirects=True)
            if not r.ok:
                return None
            low = r.text.lower()
            if date_iso not in low or "bhubaneswar" not in low:
                return None
            soup = BeautifulSoup(r.text, "html.parser")
            title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
            # The live viewer title currently identifies the edition/city.
            score = 0
            if "bhubaneswar" in title:
                score += 100
            if "bhubaneswar sambad epaper" in low:
                score += 50
            return score, edition_id, u
        except requests.RequestException:
            return None

    # Sambad edition ids are small sequential integers. Probe concurrently.
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(probe, range(1, 151)))

    valid = [x for x in results if x]
    if valid:
        valid.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return valid[0][2]

    raise RuntimeError(f"Sambad Bhubaneswar edition not found for {date_iso}")

def page_count(session, edition):
    r = get(session, edition)
    nums = [int(x) for x in re.findall(r"(?:Page\s*(?:No\.?)?\s*|pageno=)(\d+)", r.text, re.I)]
    # The viewer normally exposes all page numbers in its navigation.
    return max(nums) if nums else 17

def discover_image_template(session, edition):
    r = get(session, edition)
    soup = BeautifulSoup(r.text, "html.parser")
    candidates = []

    for tag in soup.find_all(["img", "source"]):
        for key in ("src", "data-src", "data-original", "data-full", "data-zoom-image"):
            val = tag.get(key)
            if val and "epaperimages" in val.lower() and re.search(r"\.(?:jpg|jpeg|png|webp)", val, re.I):
                candidates.append(urljoin(r.url, val))

    for raw in re.findall(r'https?://[^"\'\\\s<>]+epaperimages[^"\'\\\s<>]+\.(?:jpg|jpeg|png|webp)', r.text, re.I):
        candidates.append(raw)

    for u in candidates:
        # Reject thumbnail and turn page number into {PAGE}.
        u = re.sub(r'[-_]1s(?=\.(?:jpg|jpeg|png|webp))', '-{PAGE}', u, flags=re.I)
        u = re.sub(r'[-_]1(?=\.(?:jpg|jpeg|png|webp))', '-{PAGE}', u, flags=re.I)
        if "{PAGE}" in u:
            return u

    raise RuntimeError("Sambad original page-image template not found")

def download_sambad():
    d = datetime.now(ZoneInfo("Asia/Kolkata"))
    date_iso = d.strftime("%Y-%m-%d")
    out = Path(f"Sambad_bhubaneswar_{d:%Y%m%d}.pdf")
    session = requests.Session()
    files = []
    seen = set()

    print("=" * 60)
    print(f"📰 SAMBAD — BHUBANESWAR — {date_iso}")
    print("=" * 60)

    edition = find_edition(session, date_iso)
    total = page_count(session, edition)
    template = discover_image_template(session, edition)
    print(f"✓ Edition/source: {edition}")
    print(f"🔎 Found {total} pages")
    print(f"🔗 Original image template: {template}")

    try:
        for n in range(1, total + 1):
            url = template.replace("{PAGE}", str(n))
            # Never accept the small 's' variant.
            url = re.sub(r'(-\d+)s(\.(?:jpe?g|png|webp))$', r'\1\2', url, flags=re.I)

            result = download_image(session, url, edition)
            if not result:
                # Try direct filename variants if the template came from a viewer.
                variants = [
                    re.sub(r'-(?:\d+)(?=\.(?:jpe?g|png|webp)$)', f"-{n}", url, flags=re.I),
                    re.sub(r'-(?:\d+)s(?=\.(?:jpe?g|png|webp)$)', f"-{n}", url, flags=re.I),
                ]
                for v in variants:
                    result = download_image(session, v, edition)
                    if result:
                        break

            if not result:
                raise RuntimeError(f"Sambad: no original image for page {n}: {url}")

            if result.digest in seen:
                raise RuntimeError(f"Sambad: duplicate image detected on page {n}: {result.url}")
            seen.add(result.digest)

            path = Path(f".sambad_{n:03d}.jpg")
            path.write_bytes(result.data)
            files.append(str(path))
            print(f"✓ Page {n:02d} — {result.width}x{result.height} — {len(result.data)/1048576:.2f} MB — {result.url}")

        with out.open("wb") as f:
            f.write(img2pdf.convert(files))
        print(f"✅ Sambad PDF: {out} ({out.stat().st_size/1048576:.2f} MB)")
        return str(out)
    finally:
        for p in files:
            Path(p).unlink(missing_ok=True)

if __name__ == "__main__":
    download_sambad()
