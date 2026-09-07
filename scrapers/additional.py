import re
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
}

TIMEZONE = ZoneInfo("Asia/Kolkata")


def _today():
    return datetime.now(TIMEZONE)


def _download_pdf(session, url, output):
    response = session.get(url, headers=HEADERS, timeout=(20, 180))
    response.raise_for_status()
    data = response.content

    if not data.startswith(b"%PDF"):
        raise RuntimeError(f"Expected PDF but received non-PDF content: {url}")

    try:
        reader = PdfReader(BytesIO(data))
        pages = len(reader.pages)
    except Exception as exc:
        raise RuntimeError(f"Invalid PDF from {url}: {exc}") from exc

    if pages < 1:
        raise RuntimeError(f"PDF contains no pages: {url}")

    output.write_bytes(data)
    print(f"   ✓ PDF: {pages} pages — {len(data) / 1048576:.2f} MB")
    print(f"   ✓ URL: {url}")
    return str(output)


def _page(session, url):
    response = session.get(url, headers=HEADERS, timeout=(20, 60))
    response.raise_for_status()
    return response, BeautifulSoup(response.text, "html.parser")


def _unique_links(soup, base_url, pattern):
    seen = set()
    results = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(base_url, anchor["href"])
        if not re.search(pattern, href, re.I) or href in seen:
            continue
        seen.add(href)
        results.append((href, anchor.get_text(" ", strip=True)))
    return results


def _find_pdf_link(soup, page_url):
    for anchor in soup.find_all("a", href=True):
        href = urljoin(page_url, anchor["href"])
        text = anchor.get_text(" ", strip=True).lower()
        if href.lower().endswith(".pdf") or "full pdf" in text or "download pdf" in text:
            return href

    html = str(soup)
    matches = re.findall(
        r"https?://[^\"'\\s<>]+\.pdf(?:\?[^\"'\\s<>]*)?",
        html,
        re.I,
    )
    return matches[0] if matches else None


def download_dinalipi():
    d = _today()
    date_path = d.strftime("%Y-%m")
    date_name = d.strftime("%d-%m-%Y")
    output = Path(f"Dinalipi_bhubaneswar_{d:%Y-%m-%d}.pdf")
    url = f"https://www.dinalipiepaper.com/media/{date_path}/{date_name}-a.pdf"

    print("=" * 60)
    print(f"📰 DINALIPI — BHUBANESWAR — {d:%Y-%m-%d}")
    print("=" * 60)

    return _download_pdf(requests.Session(), url, output)


def download_odisha_bhaskar():
    d = _today()
    session = requests.Session()
    root_url = "https://epaper.odishabhaskar.com/"

    print("=" * 60)
    print(f"📰 ODISHA BHASKAR — BHUBANESWAR — {d:%Y-%m-%d}")
    print("=" * 60)

    response, soup = _page(session, root_url)
    candidates = _unique_links(soup, response.url, r"/edition/\d+/")
    if not candidates:
        raise RuntimeError("Odisha Bhaskar: no edition link found")

    edition_url = None
    edition_soup = None
    for href, _ in candidates:
        try:
            edition_response, candidate_soup = _page(session, href)
        except requests.RequestException:
            continue
        text = candidate_soup.get_text(" ", strip=True).lower()
        if "odisha bhaskar" in text and "bhubaneswar" in text:
            edition_url = edition_response.url
            edition_soup = candidate_soup
            break

    if not edition_url:
        raise RuntimeError("Odisha Bhaskar: Bhubaneswar edition not found")

    print(f"✓ Edition: {edition_url}")
    pdf_url = _find_pdf_link(edition_soup, edition_url)
    if not pdf_url:
        raise RuntimeError("Odisha Bhaskar: Full PDF link not found")

    output = Path(f"Odisha_Bhaskar_bhubaneswar_{d:%Y-%m-%d}.pdf")
    return _download_pdf(session, pdf_url, output)


def download_sanchar():
    d = _today()
    session = requests.Session()
    root_url = "https://epaper.sanchar.live/"

    print("=" * 60)
    print(f"📰 SANCHAR — {d:%Y-%m-%d}")
    print("=" * 60)

    response, soup = _page(session, root_url)
    candidates = _unique_links(soup, response.url, r"/edition/\d+/sanchar-[^\"'<>]+")
    if not candidates:
        raise RuntimeError("Sanchar: no current edition link found")

    edition_url = candidates[0][0]
    edition_response, edition_soup = _page(session, edition_url)
    edition_url = edition_response.url
    print(f"✓ Edition: {edition_url}")

    pdf_url = _find_pdf_link(edition_soup, edition_url)
    if not pdf_url:
        raise RuntimeError("Sanchar: PDF link not found")

    output = Path(f"Sanchar_{d:%Y-%m-%d}.pdf")
    return _download_pdf(session, pdf_url, output)


def download_swadhikar():
    d = _today()
    session = requests.Session()
    root_url = "https://www.swadhikar.in/epaper/"

    print("=" * 60)
    print(f"📰 SWADHIKAR — {d:%Y-%m-%d}")
    print("=" * 60)

    response, soup = _page(session, root_url)
    pdf_url = _find_pdf_link(soup, response.url)
    if not pdf_url:
        raise RuntimeError("Swadhikar: Full PDF link not found")

    output = Path(f"Swadhikar_{d:%Y-%m-%d}.pdf")
    return _download_pdf(session, pdf_url, output)


if __name__ == "__main__":
    for scraper in (
        download_dinalipi,
        download_odisha_bhaskar,
        download_sanchar,
        download_swadhikar,
    ):
        scraper()
