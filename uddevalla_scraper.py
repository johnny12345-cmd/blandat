#!/usr/bin/env python3
"""
Uddevalla Kommun PDF Protocol Scraper
======================================
Laddar ner PDF-protokoll och handlingar från Uddevalla kommuns hemsida för:
  - Kommunfullmäktige (kallelse och protokoll)
  - Kommunstyrelsen (kallelse och protokoll)

Källa 1 – Indexsidor (SiteVision fillistor):
  Uddevalla använder SiteVision. PDF-URL:er bäddas in i JSON via
  AppRegistry.registerInitialState() i <script>-taggar.
  OBS: Indexsidorna visar bara innevarande år (2026).

Källa 2 – Nyhetsarkikel:
  Äldre handlingar (fr.o.m. aug 2025) länkas från nyhetsartiklar under
  /nyheter/nyhetsarkiv/. Skriptet hämtar nyhetslistningssidan, identifierar
  relevanta artiklar och extraherar /download/-PDF-er därifrån.
  Nyhetslistningen täcker ca 6 månader bakåt; handlingar äldre än så (2022–
  tidig 2025) kan bara nås via kommunens LEX-system och ingår ej här.
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

# Källa 1: SiteVision-fillistor (visar bara aktuellt år)
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

# Källa 2: Nyhetslistning med artiklar om möten (täcker ~6 mån bakåt)
NEWS_LISTING_URL = (
    "https://www.uddevalla.se/kommun-och-politik/nyheter/"
    "nyheter-kommun-och-politik.html"
)

# Nyckelord i nyhetsartikelns URL som avgör vilken sektion PDF:en tillhör
NEWS_SECTION_KEYWORDS = {
    "kommunfullmaktige": ["kommunfullmaktige"],
    "kommunstyrelsen":   ["kommunstyrelsen"],
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
    m = re.search(r'(202[2-9]|2030)', text)
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
# Källa 1 – SiteVision-indexsidor
# ---------------------------------------------------------------------------

def get_pdf_links_from_index(
    session: requests.Session, index_url: str
) -> list[tuple[str, str, str]]:
    """
    Hämtar indexsidan och returnerar lista med (pdf_url, filnamn, year).
    Visar bara innevarande årets dokument (SiteVision-begränsning).
    """
    print(f"\nHamtar indexsida: {index_url}")
    resp = safe_get(session, index_url)
    if resp is None:
        return []

    session.headers["Referer"] = index_url
    seen_urls: set[str] = set()
    results = extract_pdf_links_from_html(resp.text, seen_urls)

    year_counts: dict[str, int] = {}
    for _, _, y in results:
        year_counts[y] = year_counts.get(y, 0) + 1

    if results:
        for y in sorted(year_counts.keys(), reverse=True):
            print(f"  Ar {y}: {year_counts[y]} PDF(er) hittade (indexsida)")
    else:
        print("  [VARNING] Inga PDF-lankar hittades pa indexsidan!")

    return results


# ---------------------------------------------------------------------------
# Källa 2 – Nyhetsarkikelter
# ---------------------------------------------------------------------------

def _section_from_filename(filename: str) -> str | None:
    """Avgör sektion utifrån PDF-filnamnet."""
    fl = filename.lower()
    if "kommunfullm" in fl:
        return "kommunfullmaktige"
    if "kommunstyrel" in fl:
        return "kommunstyrelsen"
    return None


def get_pdf_links_from_news(
    session: requests.Session,
) -> dict[str, list[tuple[str, str, str]]]:
    """
    Hämtar nyhetslistningssidan, identifierar artiklar om kommunfullmäktige
    och kommunstyrelsen, besöker varje artikel och extraherar PDF-länkar.

    Returnerar dict {section: [(pdf_url, filnamn, year), ...]}.
    """
    print(f"\nHamtar nyhetslistning: {NEWS_LISTING_URL}")
    resp = safe_get(session, NEWS_LISTING_URL)
    if resp is None:
        return {}

    session.headers["Referer"] = NEWS_LISTING_URL
    html = resp.text

    # Hitta alla nyhetsartikel-URL:er under /nyhetsarkiv/
    article_paths = re.findall(
        r'(/[^"\'<>\s]*nyhetsarkiv/\d{4}-\d{2}-\d{2}-[^"\'<>\s]+\.html)',
        html,
        re.IGNORECASE,
    )

    # Bestäm vilka artiklar som är relevanta per sektion
    section_article_urls: dict[str, list[str]] = {s: [] for s in NEWS_SECTION_KEYWORDS}
    seen_article_urls: set[str] = set()
    for path in article_paths:
        full_url = BASE_URL + path
        if full_url in seen_article_urls:
            continue
        path_lower = path.lower()
        for section, keywords in NEWS_SECTION_KEYWORDS.items():
            if any(kw in path_lower for kw in keywords):
                section_article_urls[section].append(full_url)
                seen_article_urls.add(full_url)
                break  # en artikel tillhör bara en sektion

    total_articles = sum(len(v) for v in section_article_urls.values())
    print(f"  Hittade {total_articles} relevanta nyhetsartiklar")
    for section, urls in section_article_urls.items():
        if urls:
            print(f"    {section}: {len(urls)} artiklar")

    # Besök varje artikel och extrahera PDF-er
    results: dict[str, list[tuple[str, str, str]]] = {s: [] for s in NEWS_SECTION_KEYWORDS}
    seen_pdf_urls: set[str] = set()

    for section, article_urls in section_article_urls.items():
        for article_url in article_urls:
            time.sleep(0.5)
            art_resp = safe_get(session, article_url)
            if art_resp is None:
                continue
            session.headers["Referer"] = article_url
            pdfs = extract_pdf_links_from_html(art_resp.text, seen_pdf_urls)

            for pdf_url, filename, year in pdfs:
                # Dubbel-kontroll: PDF:en bör tillhöra rätt sektion
                detected_section = _section_from_filename(filename)
                if detected_section and detected_section != section:
                    # Lägg i rätt sektion istället
                    results.setdefault(detected_section, []).append(
                        (pdf_url, filename, year)
                    )
                else:
                    results[section].append((pdf_url, filename, year))

    for section, pdfs in results.items():
        year_counts: dict[str, int] = {}
        for _, _, y in pdfs:
            year_counts[y] = year_counts.get(y, 0) + 1
        if year_counts:
            for y in sorted(year_counts.keys(), reverse=True):
                print(f"  {section} Ar {y}: {year_counts[y]} PDF(er) hittade (nyheter)")

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


def download_pdf_list(
    session: requests.Session,
    pdf_links: list[tuple[str, str, str]],
    output_dir: Path,
    referer: str,
    label: str,
) -> tuple[int, int, int]:
    """Laddar ner en lista PDF-er. Returnerar (nedladdade, hoppade, misslyckade)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    total_downloaded = total_skipped = total_failed = 0

    for pdf_url, filename, year in pdf_links:
        dest_filename = sanitize_filename(filename)
        if not dest_filename.lower().endswith(".pdf"):
            dest_filename += ".pdf"
        dest_path = output_dir / dest_filename

        already_existed = dest_path.exists()
        success = download_pdf(session, pdf_url, dest_path, referer=referer)

        if success:
            if already_existed:
                total_skipped += 1
            else:
                total_downloaded += 1
        else:
            total_failed += 1

        time.sleep(DELAY_BETWEEN_DOWNLOADS)

    return total_downloaded, total_skipped, total_failed


# ---------------------------------------------------------------------------
# Huvudflöde
# ---------------------------------------------------------------------------

def main() -> None:
    print("Uddevalla Kommun PDF-skrapare")
    print("=" * 60)
    print(f"Sparar filer till: {DOWNLOAD_ROOT}")
    print(f"Filtrerade ar: {', '.join(sorted(TARGET_YEARS))}")
    print(
        "\nOBS: Indexsidorna visar bara 2026. Aldre handlingar (aug 2025-)\n"
        "     hamtas via nyhetsartiklar. Handlingar fore aug 2025 gar\n"
        "     bara att na via kommunens LEX-system och laddas inte ner har."
    )

    session = make_session()
    print("\nInitierar session mot Uddevalla kommuns hemsida ...")
    if safe_get(session, BASE_URL):
        print("  Session initierad OK")
    else:
        print("  [Varning] Kunde inte initiera session, fortsatter anda ...")
    time.sleep(1)

    # Samla ihop alla PDF-er per sektion (dedup via seen_pdf_urls)
    all_pdfs: dict[str, list[tuple[str, str, str]]] = {
        s: [] for s in INDEX_PAGES
    }
    seen_pdf_urls: set[str] = set()

    # Källa 1: indexsidor
    print(f"\n{'='*60}")
    print("KALLA 1: INDEXSIDOR (aktuellt ar)")
    print(f"{'='*60}")
    for section_name, index_url in INDEX_PAGES.items():
        links = get_pdf_links_from_index(session, index_url)
        for item in links:
            pdf_url, filename, year = item
            if pdf_url not in seen_pdf_urls:
                seen_pdf_urls.add(pdf_url)
                all_pdfs[section_name].append(item)
        time.sleep(1)

    # Källa 2: nyhetsartiklar
    print(f"\n{'='*60}")
    print("KALLA 2: NYHETSARTIKLAR (aug 2025 och framat)")
    print(f"{'='*60}")
    news_pdfs = get_pdf_links_from_news(session)
    for section_name, links in news_pdfs.items():
        for item in links:
            pdf_url, filename, year = item
            if pdf_url not in seen_pdf_urls:
                seen_pdf_urls.add(pdf_url)
                all_pdfs.setdefault(section_name, []).append(item)

    # Ladda ner
    print(f"\n{'='*60}")
    print("LADDAR NER")
    print(f"{'='*60}")
    grand_downloaded = grand_skipped = grand_failed = 0

    for section_name, pdf_links in all_pdfs.items():
        if not pdf_links:
            print(f"\n[{section_name}] Inga handlingar hittades.")
            continue

        print(f"\n--- {section_name.upper()} ({len(pdf_links)} filer) ---")
        output_dir = DOWNLOAD_ROOT / section_name
        dl, sk, fa = download_pdf_list(
            session, pdf_links, output_dir,
            referer=INDEX_PAGES.get(section_name, BASE_URL),
            label=section_name,
        )
        grand_downloaded += dl
        grand_skipped += sk
        grand_failed += fa
        time.sleep(1)

    print(f"\n{'='*60}")
    print("TOTALT")
    print(f"{'='*60}")
    print(f"  Nedladdade  : {grand_downloaded}")
    print(f"  Hoppade over: {grand_skipped}")
    print(f"  Misslyckade : {grand_failed}")
    print("\nKlart!")


if __name__ == "__main__":
    main()
