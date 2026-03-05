#!/usr/bin/env python3
"""
Uddevalla Kommun PDF Protocol Scraper
======================================
Laddar ner PDF-protokoll och handlingar från Uddevalla kommuns hemsida för:
  - Kommunfullmäktige (kallelse och protokoll)
  - Kommunstyrelsen (kallelse och protokoll)

Uddevalla använder SiteVision – PDF-URL:er bäddas in i JSON via
AppRegistry.registerInitialState() i <script>-taggar, inte som vanliga
<a href>-taggar. URL-mönster: /download/{id}/{timestamp}/{filnamn}.pdf
Årtalet finns i filnamnet, inte i katalogsökvägen.
"""

import os
import re
import time
import urllib.parse
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

BASE_URL = "https://www.uddevalla.se"

INDEX_PAGES = {
    "kommunfullmaktige": (
        "https://www.uddevalla.se/kommun-och-politik/politik-och-demokrati/"
        "moten-och-protokoll/kallelse-och-protokoll/"
        "kommunfullmaktiges-kallelse-och-protokoll.html"
    ),
    "kommunstyrelsen": (
        "https://www.uddevalla.se/kommun-och-politik/politik-och-demokrati/"
        "moten-och-protokoll/kallelse-och-protokoll/"
        "kommunstyrelsens-kallelse-och-protokoll.html"
    ),
}

TARGET_YEARS = {"2022", "2023", "2024", "2025", "2026"}

DOWNLOAD_ROOT = Path(
    r"C:\Users\anan17\OneDrive - Sveriges Television\AI\Kommunprotokoll\Uddevalla"
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


def extract_year_from_text(text: str) -> str | None:
    """Plockar ut årtalet ur filnamnet eller URL:en."""
    m = re.search(r'(202[2-9]|2030)', text)
    return m.group(1) if m else None


def extract_date_from_text(text: str) -> str | None:
    """Försöker hitta ÅÅÅÅ-MM-DD i en sträng."""
    m = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# HTML-parsning
# ---------------------------------------------------------------------------

def get_pdf_links(session: requests.Session, index_url: str) -> list[tuple[str, str, str]]:
    """
    Hämtar indexsidan och returnerar lista med (pdf_url, filnamn, year).

    Uddevalla använder SiteVision med AppRegistry.registerInitialState() –
    PDF-URL:er bäddas in i JSON i <script>-taggar. Söker i rå HTML med regex
    efter /download/...pdf-mönster. Årtalet hämtas från filnamnet i URL:en.
    """
    print(f"\nHamtar indexsida: {index_url}")
    resp = safe_get(session, index_url)
    if resp is None:
        return []

    session.headers["Referer"] = index_url
    html = resp.text

    # Hitta alla /download/...pdf-sökvägar i rå HTML (inkl. JSON-data)
    raw_paths = re.findall(
        r'(/download/[^"\'<>\s\\]+\.pdf)',
        html,
        re.IGNORECASE,
    )

    results: list[tuple[str, str, str]] = []
    seen_urls: set[str] = set()

    for path in raw_paths:
        # JSON-escaped snedstreck (\/) -> /
        path = path.replace("\\/", "/")
        decoded_path = urllib.parse.unquote(path)
        href = BASE_URL + path

        if href in seen_urls:
            continue

        # Årtalet sitter i filnamnet, t.ex. "...kallelse 2024-03-11.pdf"
        filename = os.path.basename(decoded_path)
        year = extract_year_from_text(filename) or extract_year_from_text(decoded_path)
        if year not in TARGET_YEARS:
            continue

        seen_urls.add(href)
        results.append((href, filename, year))

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

    for pdf_url, filename, year in pdf_links:
        # Filnamnet från servern är redan beskrivande (t.ex. "Kommunstyrelsens protokoll 2024-11-18.pdf")
        dest_filename = sanitize_filename(filename)
        if not dest_filename.lower().endswith(".pdf"):
            dest_filename += ".pdf"
        dest_path = output_dir / dest_filename

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
    print("Uddevalla Kommun PDF-skrapare")
    print("=" * 60)
    print(f"Sparar filer till: {DOWNLOAD_ROOT}")
    print(f"Filtrerade ar: {', '.join(sorted(TARGET_YEARS))}")

    session = make_session()
    print("\nInitierar session mot Uddevalla kommuns hemsida ...")
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
