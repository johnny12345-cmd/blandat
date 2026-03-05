#!/usr/bin/env python3
"""
Skövde Kommun PDF Protocol Scraper
====================================
Laddar ner PDF-protokoll och handlingar från Skövde kommuns hemsida för:
  - Kommunfullmäktige (handlingar)
  - Kommunstyrelsen (protokoll)

Strategi:
PDF-länkarna (under /globalassets/) ligger direkt på indexsidorna –
inget behov av att besöka mellanliggande mötessidor.
1. Hämta indexsidan.
2. Samla alla PDF-länkar under /globalassets/.
3. Filtrera på TARGET_YEARS (år finns i URL-sökvägen).
4. Ladda ner med rätt namngivning.
"""

import os
import re
import time
import urllib.parse
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

BASE_URL = "https://www.skovde.se"

INDEX_PAGES = {
    "kommunfullmaktige": (
        "https://www.skovde.se/kommun-och-politik/sammantraden-protokoll-och-handlingar/"
        "kommunfullmaktiges-handlingar/"
    ),
    "kommunstyrelsen": (
        "https://www.skovde.se/kommun-och-politik/sammantraden-protokoll-och-handlingar/"
        "kommunstyrelsens-protokoll/"
    ),
}

TARGET_YEARS = {"2022", "2023", "2024", "2025", "2026"}

DOWNLOAD_ROOT = Path(
    r"C:\Users\anan17\OneDrive - Sveriges Television\AI\Kommunprotokoll\Skovde"
)

REQUEST_TIMEOUT = 30
DELAY_BETWEEN_DOWNLOADS = 1

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "DNT": "1",
}


# ---------------------------------------------------------------------------
# Hjälpfunktioner
# ---------------------------------------------------------------------------

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def safe_get(session: requests.Session, url: str, retries: int = 3):
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp
        except requests.exceptions.Timeout:
            print(f"  [Timeout] Forsok {attempt}/{retries}: {url}")
        except requests.exceptions.HTTPError as exc:
            print(f"  [HTTP-fel] {exc}: {url}")
            return None
        except requests.exceptions.RequestException as exc:
            print(f"  [Natverksfel] Forsok {attempt}/{retries}: {exc}")
        if attempt < retries:
            time.sleep(2 ** attempt)
    return None


def sanitize_filename(name: str) -> str:
    name = urllib.parse.unquote(name)
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r'\s+', " ", name).strip()
    if len(name) > 200:
        base, ext = os.path.splitext(name)
        name = base[:196] + ext
    return name


def extract_year_from_url(url: str) -> str | None:
    """Plockar ut årtalet från URL-sökvägen, t.ex. /globalassets/.../2024/..."""
    m = re.search(r'/(202[2-9]|2030)/', url)
    return m.group(1) if m else None


def extract_date_from_url(url: str) -> str | None:
    """Försöker hitta ÅÅÅÅ-MM-DD i URL-sökvägen."""
    m = re.search(r'(\d{4}-\d{2}-\d{2})', url)
    return m.group(1) if m else None


def build_filename(pdf_url: str, link_text: str) -> str:
    """
    Bygger filnamn på formatet ÅÅÅÅ-MM-DD_beskrivning.pdf.
    Prioriterar länktexten som beskrivning, faller tillbaka på URL-filnamnet.
    """
    url_path = urllib.parse.urlparse(pdf_url).path
    original_name = os.path.basename(urllib.parse.unquote(url_path))
    if not original_name.lower().endswith(".pdf"):
        original_name += ".pdf"

    # Datum: försök i URL, annars i länktext
    date_str = extract_date_from_url(pdf_url) or re.search(
        r'\d{4}-\d{2}-\d{2}', link_text
    )
    if hasattr(date_str, 'group'):
        date_str = date_str.group(0)

    description = sanitize_filename(
        link_text.strip() if link_text.strip() else os.path.splitext(original_name)[0]
    )

    if date_str and isinstance(date_str, str):
        filename = f"{date_str}_{description}.pdf"
    else:
        year = extract_year_from_url(pdf_url) or "okant_ar"
        filename = f"{year}_{description}.pdf"

    return sanitize_filename(filename)


# ---------------------------------------------------------------------------
# HTML-parsning
# ---------------------------------------------------------------------------

def get_pdf_links(session: requests.Session, index_url: str) -> list[tuple[str, str, str]]:
    """
    Hämtar indexsidan och returnerar lista med (pdf_url, link_text, year).

    Skövde använder Next.js – PDF-URL:er bäddas in i JSON i <script>-taggar,
    inte som vanliga <a href>-taggar. Därför söker vi direkt i rå HTML-text
    med regex efter /globalassets/...pdf-mönster.
    """
    print(f"\nHamtar indexsida: {index_url}")
    resp = safe_get(session, index_url)
    if resp is None:
        return []

    session.headers["Referer"] = index_url
    html = resp.text

    # Hitta alla unika /globalassets/...pdf-sökvägar i rå HTML (inkl. JSON-data)
    raw_paths = re.findall(
        r'(/globalassets/[^"\'<>\s\\]+\.pdf)',
        html,
        re.IGNORECASE,
    )

    results: list[tuple[str, str, str]] = []
    seen_urls: set[str] = set()

    for path in raw_paths:
        # JSON-escaped snedstreck (\/) -> /
        path = path.replace("\\/", "/")
        path = urllib.parse.unquote(path)
        href = BASE_URL + path

        if href in seen_urls:
            continue

        year = extract_year_from_url(href)
        if year not in TARGET_YEARS:
            continue

        seen_urls.add(href)
        # Länktext: bygg från filnamnet i URL:en (ingen riktig länktext tillgänglig)
        filename_base = os.path.splitext(os.path.basename(path))[0]
        link_text = filename_base.replace("-", " ").replace("_", " ")
        results.append((href, link_text, year))

    # Statistik per år
    year_counts: dict[str, int] = {}
    for _, _, y in results:
        year_counts[y] = year_counts.get(y, 0) + 1

    if results:
        for y in sorted(year_counts.keys(), reverse=True):
            print(f"  Ar {y}: {year_counts[y]} PDF(er) hittade")
    else:
        print("  [VARNING] Inga PDF-lankar hittades!")

    return results


# ---------------------------------------------------------------------------
# Nedladdning
# ---------------------------------------------------------------------------

def download_pdf(
    session: requests.Session,
    pdf_url: str,
    dest_path: Path,
    referer: str,
) -> bool:
    if dest_path.exists():
        print(f"  [Hoppar over] Finns redan: {dest_path.name}")
        return True

    session.headers["Referer"] = referer
    print(f"  Laddar ner -> {dest_path.name}")

    try:
        with session.get(pdf_url, timeout=REQUEST_TIMEOUT, stream=True) as resp:
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            if "html" in content_type and "pdf" not in content_type:
                print(f"  [Varning] Ovantat Content-Type '{content_type}'")
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        size_kb = dest_path.stat().st_size / 1024
        print(f"  [OK] {size_kb:.1f} kB sparad")
        return True
    except requests.exceptions.Timeout:
        print(f"  [Timeout] {pdf_url}")
    except requests.exceptions.HTTPError as exc:
        print(f"  [HTTP-fel] {exc}")
    except OSError as exc:
        print(f"  [I/O-fel] {exc}")

    if dest_path.exists():
        dest_path.unlink()
    return False


# ---------------------------------------------------------------------------
# Huvudflöde
# ---------------------------------------------------------------------------

def scrape_section(
    session: requests.Session,
    section_name: str,
    index_url: str,
    output_dir: Path,
) -> None:
    print(f"\n{'='*60}")
    print(f"SEKTION: {section_name.upper()}")
    print(f"{'='*60}")

    output_dir.mkdir(parents=True, exist_ok=True)

    pdf_links = get_pdf_links(session, index_url)

    if not pdf_links:
        print(f"[Varning] Inga PDF-filer hittades for {section_name}.")
        return

    total_downloaded = total_skipped = total_failed = 0

    for pdf_url, link_text, year in pdf_links:
        filename = build_filename(pdf_url, link_text)
        dest_path = output_dir / filename

        already_existed = dest_path.exists()
        success = download_pdf(session, pdf_url, dest_path, referer=index_url)

        if success:
            if already_existed:
                total_skipped += 1
            else:
                total_downloaded += 1
        else:
            total_failed += 1

        time.sleep(DELAY_BETWEEN_DOWNLOADS)

    print(f"\n[Sammanfattning - {section_name}]")
    print(f"  Nedladdade  : {total_downloaded}")
    print(f"  Hoppade over: {total_skipped}")
    print(f"  Misslyckade : {total_failed}")


def main() -> None:
    print("Skovde Kommun PDF-skrapare")
    print("=" * 60)
    print(f"Sparar filer till: {DOWNLOAD_ROOT}")
    print(f"Filtrerade ar: {', '.join(sorted(TARGET_YEARS))}")

    session = make_session()
    print("\nInitierar session mot Skovde kommuns hemsida ...")
    if safe_get(session, BASE_URL):
        print("  Session initierad OK")
    else:
        print("  [Varning] Kunde inte initiera session, fortsatter anda ...")
    time.sleep(1)

    for section_name, index_url in INDEX_PAGES.items():
        output_dir = DOWNLOAD_ROOT / section_name
        scrape_section(session, section_name, index_url, output_dir)
        time.sleep(2)

    print("\nKlart!")


if __name__ == "__main__":
    main()
