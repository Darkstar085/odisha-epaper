
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
    # Current Samaja viewer uses /epaper/1/{edition}/{date}/{page}.
    url = f"{BASE}/indexnext.php?mod=1"
    r = fetch(session, url)
    patterns = [
        rf'href=["\']([^"\']*/epaper/1/(\d+)/{re.escape(date_iso)}/1)["\']',
        rf'["\']([^"\']*/epaper/1/(\d+)/{re.escape(date_iso)}/1)["\']',
    ]
    for pat in patterns:
        m = re.search(pat, r.text, re.I)
        if m:
            return urljoin(r.url, m.group(1)), m.group(2)
    # Known public route can still be discovered from page links.
    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.search(r"/epaper/1/(\d+)/" + re.escape(date_iso) + r"/1", href)
        if m:
            return urljoin(r.url, href), m.group(1)
    raise RuntimeError(f"Samaja edition not found for {date_iso}")

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
