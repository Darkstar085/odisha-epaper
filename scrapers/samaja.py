
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader, PdfWriter

BASE = "https://www.samajaepaper.in"
HEADERS = {"User-Agent": "Mozilla/5.0 Chrome/128.0 Safari/537.36"}

def fetch(session, url):
    r = session.get(url, headers=HEADERS, timeout=(20, 60))
    r.raise_for_status()
    return r

def discover_edition(session, date_iso):
    """Discover Samaja's Bhubaneswar edcode for the requested date.

    Samaja's edcode is not the same as its public /epaper/ edition id and
    changes between editions. The viewer accepts:
      /indexnext.php?edcode=NN&mod=1&pagedate=YYYY-MM-DD&pgnum=1

    We first inspect the main viewer/archive for explicit date/edcode links,
    then fall back to probing the small current edcode range and identifying
    the page whose title/metadata says Bhubaneswar.
    """
    import concurrent.futures

    base_urls = [
        f"{BASE}/indexnext.php?mod=1&pagedate={date_iso}&pgnum=1",
        f"https://m.samajaepaper.in/indexnext.php?mod=1&pagedate={date_iso}&pgnum=1",
        BASE,
        "https://m.samajaepaper.in/",
    ]

    for index_url in base_urls:
        try:
            r = fetch(session, index_url)
        except requests.RequestException:
            continue

        # Explicit viewer links are the best source.
        matches = re.findall(
            rf'(?:href|data-url|data-href)=["\']([^"\']*edcode=\d+[^"\']*pagedate={re.escape(date_iso)}[^"\']*)["\']',
            r.text, re.I
        )
        for href in matches:
            u = urljoin(r.url, href)
            if re.search(r"(?:^|[?&])edcode=\d+", u, re.I):
                # Confirm that this is actually the Bhubaneswar edition.
                try:
                    vr = fetch(session, u)
                    title = vr.text.lower()
                    if "bhubaneswar" in title:
                        return u, re.search(r"(?:^|[?&])edcode=(\d+)", u, re.I).group(1)
                except requests.RequestException:
                    pass

    # Current Samaja edcodes are compact and sequential. Probe a bounded
    # range concurrently so a changed edcode does not break the pipeline.
    def probe(code):
        u = (
            f"https://m.samajaepaper.in/indexnext.php?"
            f"edcode={code}&mod=1&pagedate={date_iso}&pgnum=1"
        )
        try:
            r = session.get(u, headers=HEADERS, timeout=(10, 25))
            if not r.ok:
                return None
            text = r.text.lower()
            if "bhubaneswar" not in text:
                return None

            # Prefer an explicit page title / edition marker containing the city.
            soup = BeautifulSoup(r.text, "html.parser")
            title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
            body = soup.get_text(" ", strip=True).lower()
            if "bhubaneswar" in title or "bhubaneswar" in body:
                return u, str(code)
        except requests.RequestException:
            return None
        return None

    # Keep the range tight enough for CI but broad enough to survive changes.
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(probe, range(1, 121)))

    # If multiple pages mention the city in navigation, choose the one whose
    # title explicitly identifies Bhubaneswar; otherwise prefer the first
    # valid candidate after sorting by edcode.
    valid = [x for x in results if x]
    if valid:
        # Re-fetch candidates and score explicit title/body matches.
        scored = []
        for u, code in valid:
            try:
                r = session.get(u, headers=HEADERS, timeout=(10, 25))
                low = r.text.lower()
                score = 0
                if "<title" in low and "bhubaneswar" in low.split("</title>", 1)[0]:
                    score += 100
                if "bhubaneswar samaja" in low:
                    score += 50
                scored.append((score, int(code), u, code))
            except requests.RequestException:
                pass
        if scored:
            scored.sort(reverse=True)
            _, _, u, code = scored[0]
            return u, code

    raise RuntimeError(f"Samaja Bhubaneswar edition not found for {date_iso}")

def discover_page_base(session, edition):
    r = fetch(session, edition)
    m = re.search(r'https?://[^"\']*epaperimages[^"\']+?\.jpg', r.text, re.I)
    if not m:
        m = re.search(r'(?:https?:)?//[^"\']*epaperimages[^"\']+?\.jpg', r.text, re.I)
    if not m:
        raise RuntimeError("Samaja page image base not found")
    return m.group(0).replace("\\/", "/")

def make_pdf_url(image_url, page_no):
    # Preserve the publisher's path/date/edition and replace the page suffix.
    u = re.sub(r'-(\d+)\.jpg(?:\?.*)?$', f"-{page_no}.pdf", image_url, flags=re.I)
    if u.lower().endswith(".pdf"):
        return u
    return re.sub(r'\.(?:jpe?g|png|webp)(?:\?.*)?$', ".pdf", image_url, flags=re.I)

def download_samaja():
    d = datetime.now(ZoneInfo("Asia/Kolkata"))
    date_iso = d.strftime("%Y-%m-%d")
    out = Path(f"Samaja_bhubaneswar_{d:%Y%m%d}.pdf")
    tmp = []
    session = requests.Session()

    print("=" * 60)
    print(f"📰 SAMAJA — BHUBANESWAR — {date_iso}")
    print("=" * 60)

    edition, edition_id = discover_edition(session, date_iso)
    print(f"✓ Edition: {edition}")

    page = fetch(session, edition)
    nums = [int(x) for x in re.findall(r"(?:Page\s*(?:No\.?)?\s*|pageno=)(\d+)", page.text, re.I)]
    total = max(nums) if nums else 23
    print(f"🔎 Found {total} pages")

    base = discover_page_base(session, edition)
    print(f"🔗 Page-image base: {base}")

    # Normalize duplicate slashes only for path construction; retain host.
    base = re.sub(r"-\d+\.jpg$", "-{PAGE}.jpg", base, flags=re.I)

    writer = PdfWriter()
    try:
        for n in range(1, total + 1):
            image_url = base.replace("{PAGE}", str(n))
            pdf_url = make_pdf_url(image_url, n)
            r = session.get(pdf_url, headers=HEADERS, timeout=(20, 120))
            if not r.ok or not r.content.startswith(b"%PDF"):
                raise RuntimeError(f"Samaja native PDF unavailable for page {n}: {pdf_url}")
            path = Path(f".samaja_{n:03d}.pdf")
            path.write_bytes(r.content)
            reader = PdfReader(str(path))
            for p in reader.pages:
                writer.add_page(p)
            tmp.append(path)
            print(f"   ✓ Native PDF page {n:02d} — {len(r.content)/1048576:.2f} MB — {pdf_url}")

        with out.open("wb") as f:
            writer.write(f)
        print(f"✅ Samaja PDF: {out} ({out.stat().st_size/1048576:.2f} MB)")
        return str(out)
    finally:
        for p in tmp:
            p.unlink(missing_ok=True)

if __name__ == "__main__":
    download_samaja()
