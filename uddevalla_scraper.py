#!/usr/bin/env python3
"""
Uddevalla Kommun – Kommunfullmäktige PDF-skrapare (2024–2026)
=============================================================
Laddar ner kallelser och protokoll för Kommunfullmäktige från
Uddevalla kommuns hemsida.

Källa 1 – SiteVision-indexsida (visar bara innevarande år):
  https://www.uddevalla.se/.../kommunfullmaktiges-kallelse-och-protokoll.html
  PDF-URL:er bäddas in i JSON via AppRegistry.registerInitialState().

Källa 2 – Nyhetslistning (täcker ~6–12 månader bakåt):
  Artiklar om kommunfullmäktige under /nyhetsarkiv/ länkas PDF-filer.
  Skriptet paginerar genom alla tillgängliga nyhetssidor (?page=N)
  tills inga nya relevanta artiklar hittas.

BEGRÄNSNING:
  Dokument äldre än det som nyhetslistningen täcker (typiskt jan–aug 2025
  och hela 2024) publiceras inte öppet på webben. De finns i kommunens
  LEX-system (kräver inloggning) och kan inte laddas ner med detta skript.
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

INDEX_URL = (
    "https://www.uddevalla.se/kommun-och-politik/politik-och-demokrati/"
    "moten-och-protokoll/kallelse-och-protokoll/"
    "kommunfullmaktiges-kallelse-och-protokoll.html"
)

NEWS_LISTING_BASE = (
    "https://www.uddevalla.se/kommun-och-politik/nyheter/"
    "nyheter-kommun-och-politik.html"
)

# Nyckelord som en nyhetsartikel-URL måste innehålla för att räknas
NEWS_KEYWORDS = ["kommunfullmaktige"]

TARGET_YEARS = {"2024", "2025", "2026"}

# Antal nyhetssidor att söka igenom (sida 1 = ingen page-param, sedan ?page=2 …)
MAX_NEWS_PAGES = 20

DOWNLOAD_ROOT = Path(
    r"C:\Users\anan17\OneDrive - Sveriges Television\AI\Kommunprotokoll\Uddevalla"
) / "kommunfullmaktige"

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
    m = re.search(r'(202[2-9])', text)
    return m.group(1) if m else None


def extract_pdf_links_from_html(
    html: str,
    seen_urls: set[str],
) -> list[tuple[str, str, str]]:
    """Extraherar /download/...pdf-länkar ur rå HTML och filtrerar på TARGET_YEARS."""
    raw_paths = re.findall(
        r'(/download/[^"\'<>\s\\]+\.pdf)',
        html,
        re.IGNORECASE,
    )
    results = []
    for path in raw_paths:
        path = path.replace("\\/", "/")
        decoded_path = urllib.parse.unquote(path)
        href = BASE_URL + path
        if href in seen_urls:
            continue
        filename = os.path.basename(decoded_path)
        year = extract_year_from_text(filename) or extract_year_from_text(decoded_path)
        if year not in TARGET_YEARS:
            continue
        seen_urls.add(href)
        results.append((href, filename, year))
    return results


# ---------------------------------------------------------------------------
# Källa 1 – SiteVision-indexsida
# ---------------------------------------------------------------------------

def get_pdf_links_from_index(
    session: requests.Session,
    seen_urls: set[str],
) -> list[tuple[str, str, str]]:
    """Hämtar indexsidan (visar bara innevarande år)."""
    print(f"\nHamtar indexsida: {INDEX_URL}")
    resp = safe_get(session, INDEX_URL)
    if resp is None:
        return []

    session.headers["Referer"] = INDEX_URL
    results = extract_pdf_links_from_html(resp.text, seen_urls)

    if results:
        year_counts: dict[str, int] = {}
        for _, _, y in results:
            year_counts[y] = year_counts.get(y, 0) + 1
        for y in sorted(year_counts.keys(), reverse=True):
            print(f"  Ar {y}: {year_counts[y]} PDF(er) hittade (indexsida)")
    else:
        print("  [VARNING] Inga PDF-lankar hittades pa indexsidan!")

    return results


# ---------------------------------------------------------------------------
# Källa 2 – Nyhetslistning med paginering
# ---------------------------------------------------------------------------

def _collect_article_urls(session: requests.Session) -> list[str]:
    """
    Paginerar igenom nyhetslistningen och returnerar URL:er till alla
    kommunfullmäktige-artiklar som hittas.
    """
    all_article_urls: list[str] = []
    seen_article_urls: set[str] = set()

    for page_num in range(1, MAX_NEWS_PAGES + 1):
        if page_num == 1:
            listing_url = NEWS_LISTING_BASE
        else:
            listing_url = f"{NEWS_LISTING_BASE}?page={page_num}"

        print(f"  Nyhetslistning sida {page_num}: {listing_url}")
        resp = safe_get(session, listing_url)
        if resp is None:
            print(f"  Kunde inte hamta sida {page_num}, avslutar paginering.")
            break

        html = resp.text
        article_paths = re.findall(
            r'(/[^"\'<>\s]*nyhetsarkiv/\d{4}-\d{2}-\d{2}-[^"\'<>\s]+\.html)',
            html,
            re.IGNORECASE,
        )

        new_found = 0
        for path in article_paths:
            full_url = BASE_URL + path
            if full_url in seen_article_urls:
                continue
            path_lower = path.lower()
            if any(kw in path_lower for kw in NEWS_KEYWORDS):
                all_article_urls.append(full_url)
                seen_article_urls.add(full_url)
                new_found += 1

        print(f"    Hittade {new_found} nya kommunfullmaktige-artiklar pa sida {page_num}")

        # Stoppa om inga nya artiklar eller om sidan inte har nyhetsarkiv-lankar alls
        if not article_paths:
            print("  Inga fler artiklar pa denna sida, avslutar paginering.")
            break

        time.sleep(0.5)

    return all_article_urls


def get_pdf_links_from_news(
    session: requests.Session,
    seen_urls: set[str],
) -> list[tuple[str, str, str]]:
    """
    Paginerar nyhetslistningen, besöker varje kommunfullmäktige-artikel
    och extraherar PDF-er.
    """
    print(f"\nHamtar nyhetsartiklar (upp till {MAX_NEWS_PAGES} sidor) ...")
    article_urls = _collect_article_urls(session)
    print(f"\n  Totalt {len(article_urls)} kommunfullmaktige-artiklar hittade")

    results: list[tuple[str, str, str]] = []

    for article_url in article_urls:
        time.sleep(0.5)
        art_resp = safe_get(session, article_url)
        if art_resp is None:
            continue
        session.headers["Referer"] = article_url
        pdfs = extract_pdf_links_from_html(art_resp.text, seen_urls)
        if pdfs:
            print(f"  {article_url.split('/')[-1]}: {len(pdfs)} PDF(er)")
        results.extend(pdfs)

    year_counts: dict[str, int] = {}
    for _, _, y in results:
        year_counts[y] = year_counts.get(y, 0) + 1
    for y in sorted(year_counts.keys(), reverse=True):
        print(f"  Ar {y}: {year_counts[y]} PDF(er) hittade (nyheter)")

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

def main() -> None:
    print("Uddevalla Kommunfullmaktige – Kallelser och Protokoll 2024–2026")
    print("=" * 60)
    print(f"Sparar filer till: {DOWNLOAD_ROOT}")
    print(f"Filtrerade ar: {', '.join(sorted(TARGET_YEARS))}")
    print(
        "\nOBS: Indexsidan visar bara aktuellt ar (2026)."
        "\n     2025 hamtas via nyhetsartiklar (tillbaka ~6-12 man)."
        "\n     2024 ar sannolikt ej tillgangligt via oppet webb –"
        "\n     dessa dokument kravs via kommunens LEX-system."
    )

    session = make_session()
    print("\nInitierar session ...")
    if safe_get(session, BASE_URL):
        print("  OK")
    else:
        print("  [Varning] Kunde inte initiera session, fortsatter anda ...")
    time.sleep(1)

    seen_pdf_urls: set[str] = set()
    all_pdfs: list[tuple[str, str, str]] = []

    # Källa 1: indexsida (2026)
    print(f"\n{'='*60}")
    print("KALLA 1: INDEXSIDA (aktuellt ar)")
    print(f"{'='*60}")
    all_pdfs.extend(get_pdf_links_from_index(session, seen_pdf_urls))
    time.sleep(1)

    # Källa 2: nyhetsartiklar (2025 och eventuellt 2024)
    print(f"\n{'='*60}")
    print("KALLA 2: NYHETSARTIKLAR")
    print(f"{'='*60}")
    all_pdfs.extend(get_pdf_links_from_news(session, seen_pdf_urls))

    # Sammanfattning av vad som hittades
    print(f"\n{'='*60}")
    print(f"TOTALT HITTADE: {len(all_pdfs)} PDF(er)")
    year_summary: dict[str, int] = {}
    for _, _, y in all_pdfs:
        year_summary[y] = year_summary.get(y, 0) + 1
    for y in sorted(year_summary.keys(), reverse=True):
        print(f"  {y}: {year_summary[y]} fil(er)")
    print(f"{'='*60}")

    if not all_pdfs:
        print("\nInga handlingar hittades. Avslutar.")
        return

    # Ladda ner
    print(f"\n{'='*60}")
    print("LADDAR NER")
    print(f"{'='*60}")
    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)

    total_downloaded = total_skipped = total_failed = 0
    for pdf_url, filename, year in all_pdfs:
        dest_filename = sanitize_filename(filename)
        if not dest_filename.lower().endswith(".pdf"):
            dest_filename += ".pdf"
        dest_path = DOWNLOAD_ROOT / dest_filename

        already_existed = dest_path.exists()
        success = download_pdf(session, pdf_url, dest_path, referer=INDEX_URL)

        if success:
            if already_existed:
                total_skipped += 1
            else:
                total_downloaded += 1
        else:
            total_failed += 1

        time.sleep(DELAY_BETWEEN_DOWNLOADS)

    print(f"\n{'='*60}")
    print("TOTALT")
    print(f"{'='*60}")
    print(f"  Nedladdade  : {total_downloaded}")
    print(f"  Hoppade over: {total_skipped}")
    print(f"  Misslyckade : {total_failed}")
    print("\nKlart!")


if __name__ == "__main__":
    main()
