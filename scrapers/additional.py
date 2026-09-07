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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

HTML_HEADERS = {
    **HEADERS,
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

TIMEZONE = ZoneInfo("Asia/Kolkata")


def _today():
    return datetime.now(TIMEZONE)


def _download_pdf(session, url, output, referer=None):
    headers = {
        **HEADERS,
        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
    }
    if referer:
        headers["Referer"] = referer
        headers["Sec-Fetch-Site"] = "same-origin"

    response = session.get(url, headers=headers, timeout=(20, 180))
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


def _page(session, url, referer=None):
    headers = {**HTML_HEADERS}
    if referer:
        headers["Referer"] = referer
        headers["Sec-Fetch-Site"] = "same-origin"
    response = session.get(url, headers=headers, timeout=(20, 60))
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
    root_url = "https://www.dinalipiepaper.com/"
    url = f"https://www.dinalipiepaper.com/media/{date_path}/{date_name}-a.pdf"

    print("=" * 60)
    print(f"📰 DINALIPI — BHUBANESWAR — {d:%Y-%m-%d}")
    print("=" * 60)

    session = requests.Session()
    # Establish the normal public e-paper session first; the PDF endpoint
    # rejects bare cross-origin requests from some runner IPs.
    root_response, _ = _page(session, root_url)
    return _download_pdf(session, url, output, referer=root_response.url)


def download_odisha_bhaskar():
    d = _today()
    session = requests.Session()
    root_urls = (
        "https://bhaskarepaper.in/",
        "https://epaper.odishabhaskar.com/",
    )

    print("=" * 60)
    print(f"📰 ODISHA BHASKAR — BHUBANESWAR — {d:%Y-%m-%d}")
    print("=" * 60)

    last_error = None
    for root_url in root_urls:
        try:
            response, soup = _page(session, root_url)
        except requests.RequestException as exc:
            last_error = exc
            continue

        candidates = _unique_links(soup, response.url, r"/edition/\d+/")
        if not candidates:
            continue

        for href, _ in candidates:
            try:
                edition_response, candidate_soup = _page(session, href, response.url)
            except requests.RequestException as exc:
                last_error = exc
                continue

            text = candidate_soup.get_text(" ", strip=True).lower()
            if "odisha bhaskar" not in text or "bhubaneswar" not in text:
                continue

            edition_url = edition_response.url
            print(f"✓ Edition: {edition_url}")
            pdf_url = _find_pdf_link(candidate_soup, edition_url)
            if not pdf_url:
                continue

            output = Path(f"Odisha_Bhaskar_bhubaneswar_{d:%Y-%m-%d}.pdf")
            try:
                return _download_pdf(session, pdf_url, output, referer=edition_url)
            except requests.RequestException as exc:
                last_error = exc

    if last_error:
        raise RuntimeError(f"Odisha Bhaskar: could not access current e-paper: {last_error}") from last_error
    raise RuntimeError("Odisha Bhaskar: Bhubaneswar edition or Full PDF link not found")


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
    edition_response, edition_soup = _page(session, edition_url, response.url)
    edition_url = edition_response.url
    print(f"✓ Edition: {edition_url}")

    pdf_url = _find_pdf_link(edition_soup, edition_url)
    if not pdf_url:
        raise RuntimeError("Sanchar: PDF link not found")

    output = Path(f"Sanchar_{d:%Y-%m-%d}.pdf")
    return _download_pdf(session, pdf_url, output, referer=edition_url)


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
    return _download_pdf(session, pdf_url, output, referer=response.url)


if __name__ == "__main__":
    for scraper in (
        download_dinalipi,
        download_odisha_bhaskar,
        download_sanchar,
        download_swadhikar,
    ):
        scraper()
