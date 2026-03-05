#!/usr/bin/env python3
"""
Borås Stad PDF Protocol Scraper
================================
Laddar ner PDF-protokoll från Borås Stads hemsida för:
  - Kommunfullmäktige
  - Kommunstyrelsen

Strategi (Sitevision CMS):
1. Hämta indexsidan, identifiera möteslänkar grupperade under h3-rubriker med årtalen.
2. Besök varje mötes-HTML-sida och plocka ut /download/-PDF-länkar.
3. Ladda ner PDFerna med rätt namngivning och felhantering.
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

BASE_URL = "https://www.boras.se"

INDEX_PAGES = {
    "kommunfullmaktige": (
        "https://www.boras.se/kommunochpolitik/kommunensorganisation/"
        "kommunfullmaktige/handlingarochprotokollkommunfullmaktige."
        "4.1fef6289155e1318edf1824d.html"
    ),
    "kommunstyrelsen": (
        "https://www.boras.se/kommunochpolitik/kommunensorganisation/"
        "kommunstyrelsen/handlingarochprotokollkommunstyrelsen."
        "4.25d9ccc5150fb715b722801e.html"
    ),
}

# Filtrera endast dessa år
TARGET_YEARS = {"2022", "2023", "2024", "2025", "2026"}

# Rotkatalog för nedladdningar (Windows-sökväg, justeras vid behov)
DOWNLOAD_ROOT = Path(
    r"C:\Users\anan17\OneDrive - Sveriges Television\AI\Kommunprotokoll\Borås"
)

REQUEST_TIMEOUT = 30          # sekunder
DELAY_BETWEEN_DOWNLOADS = 1   # sekund

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
    """Skapar en session med gemensamma headers och cookie-hantering."""
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def safe_get(session: requests.Session, url: str, retries: int = 3) -> requests.Response | None:
    """GET med timeout och enkla omförsök."""
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp
        except requests.exceptions.Timeout:
            print(f"  [Timeout] Försök {attempt}/{retries}: {url}")
        except requests.exceptions.HTTPError as exc:
            print(f"  [HTTP-fel] {exc}: {url}")
            return None
        except requests.exceptions.RequestException as exc:
            print(f"  [Nätverksfel] Försök {attempt}/{retries}: {exc}")
        if attempt < retries:
            time.sleep(2 ** attempt)
    return None


def sanitize_filename(name: str) -> str:
    """Tar bort/ersätter otillåtna tecken i filnamn (Windows-kompatibelt)."""
    name = urllib.parse.unquote(name)
    # Behåll svenska bokstäver och vanliga tecken
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r'\s+', " ", name).strip()
    # Begränsa längd (Windows MAX_PATH)
    if len(name) > 200:
        base, ext = os.path.splitext(name)
        name = base[:196] + ext
    return name


def extract_date_from_text(text: str) -> str | None:
    """
    Försöker hitta ett datum (ÅÅÅÅ-MM-DD) i en textsträng.
    Hanterar format som '2026-01-22', '22 jan 2026', '2026-01-22 17:00' m.fl.
    Returnerar 'ÅÅÅÅ-MM-DD' eller None.
    """
    # Direkt ISO-format
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # Datum i URL-format: yyyymmdd
    m = re.search(r'(\d{4})(\d{2})(\d{2})\d{4}', text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    return None


def build_filename(pdf_url: str, link_text: str, year: str) -> str:
    """
    Bygger ett filnamn på formatet ÅÅÅÅ-MM-DD_beskrivning.pdf.
    Om inget datum hittas används År_Originalnamn.pdf.
    """
    # Originalfilnamnet från URL:en
    url_path = urllib.parse.urlparse(pdf_url).path
    original_name = os.path.basename(urllib.parse.unquote(url_path))
    if not original_name.lower().endswith(".pdf"):
        original_name += ".pdf"

    # Försök hitta datum i länktexten eller i originalfilnamnet
    date_str = extract_date_from_text(link_text) or extract_date_from_text(original_name)

    if date_str:
        # Bygg beskrivning från länktexten (eller originalnamnet om länktext är tom)
        description = sanitize_filename(link_text.strip() or os.path.splitext(original_name)[0])
        filename = f"{date_str}_{description}.pdf"
    else:
        base = sanitize_filename(os.path.splitext(original_name)[0])
        filename = f"{year}_{base}.pdf"

    return sanitize_filename(filename)


# ---------------------------------------------------------------------------
# HTML-parsning
# ---------------------------------------------------------------------------

def get_meeting_links_by_year(session: requests.Session, index_url: str) -> dict[str, list[str]]:
    """
    Hämtar indexsidan och returnerar en dict {år: [absoluta mötessidans-URL, ...]}.
    Strukturen på sidan: h3-rubriker med årtalen, följt av <ul> med möteslänkar.
    """
    print(f"\nHämtar indexsida: {index_url}")
    resp = safe_get(session, index_url)
    if resp is None:
        return {}

    # Sätt Referer för efterföljande anrop
    session.headers["Referer"] = index_url

    soup = BeautifulSoup(resp.text, "html.parser")
    year_meetings: dict[str, list[str]] = {}

    # Identifiera alla h3-element med årtalen
    for heading in soup.find_all(["h2", "h3", "h4"]):
        heading_text = heading.get_text(strip=True)

        # Kontrollera om rubriken är ett av de önskade årtalen
        if heading_text not in TARGET_YEARS:
            continue

        year = heading_text
        meetings: list[str] = []

        # Iterera syskon-element EFTER rubriken tills nästa rubrik på samma nivå
        for sibling in heading.find_next_siblings():
            tag_name = sibling.name
            if tag_name in ("h2", "h3", "h4"):
                break  # Nästa årsrubrik – sluta

            # Plocka ut alla <a>-taggar som pekar på mötessidor (inte PDF)
            for a_tag in sibling.find_all("a", href=True):
                href = a_tag["href"]
                if ".pdf" in href.lower() or "/download/" in href.lower():
                    continue  # Direkt PDF-länk – hanteras separat
                if href.startswith("/"):
                    href = BASE_URL + href
                if href.startswith(BASE_URL):
                    meetings.append(href)

        if meetings:
            # Avduplicera men behåll ordning
            seen: set[str] = set()
            unique_meetings = []
            for m in meetings:
                if m not in seen:
                    seen.add(m)
                    unique_meetings.append(m)
            year_meetings[year] = unique_meetings
            print(f"  År {year}: {len(unique_meetings)} möten hittade")

    return year_meetings


def _url_is_pdf(href: str) -> bool:
    """
    Avgör om en URL pekar på en PDF-fil.
    Kontrollerar filnamnsändelsen i URL-sökvägen (hanterar URL-kodning).
    """
    path = urllib.parse.urlparse(href).path
    decoded_path = urllib.parse.unquote(path).lower()
    return decoded_path.endswith(".pdf")


def get_pdf_links_from_meeting_page(
    session: requests.Session, meeting_url: str, referer: str
) -> list[tuple[str, str]]:
    """
    Besöker en mötessida och returnerar lista med (pdf_url, link_text).
    Letar efter href:ar vars sökväg slutar med '.pdf' (URL-avkodad).
    Filtrerar bort .docx och andra icke-PDF-filer som även kan ligga under /download/.
    """
    session.headers["Referer"] = referer
    resp = safe_get(session, meeting_url)
    if resp is None:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    pdf_links: list[tuple[str, str]] = []
    seen_urls: set[str] = set()

    for a_tag in soup.find_all("a", href=True):
        href: str = a_tag["href"]
        text: str = a_tag.get_text(strip=True)

        # Säkerställ absolut URL
        if href.startswith("/"):
            href = BASE_URL + href
        elif not href.startswith("http"):
            href = urllib.parse.urljoin(meeting_url, href)

        # Filtrera: sökvägen måste sluta med .pdf (skyddar mot .docx, .xlsx m.fl.)
        if not _url_is_pdf(href):
            continue

        if href in seen_urls:
            continue
        seen_urls.add(href)

        pdf_links.append((href, text))

    return pdf_links


# ---------------------------------------------------------------------------
# Nedladdning
# ---------------------------------------------------------------------------

def download_pdf(
    session: requests.Session,
    pdf_url: str,
    dest_path: Path,
    referer: str,
) -> bool:
    """
    Laddar ner en PDF till dest_path. Hoppar över om filen redan finns.
    Returnerar True vid lyckad nedladdning, False annars.
    """
    if dest_path.exists():
        print(f"  [Hoppar över] Finns redan: {dest_path.name}")
        return True

    session.headers["Referer"] = referer
    print(f"  Laddar ner → {dest_path.name}")

    try:
        with session.get(pdf_url, timeout=REQUEST_TIMEOUT, stream=True) as resp:
            resp.raise_for_status()

            # Kontrollera att det verkligen är en PDF (content-type)
            content_type = resp.headers.get("Content-Type", "")
            if "html" in content_type and "pdf" not in content_type:
                print(f"  [Varning] Oväntat Content-Type '{content_type}' för {pdf_url}")

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

    # Ta bort ofullständig fil
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
    """
    Komplett flöde för en sektion (kommunfullmäktige eller kommunstyrelsen).
    """
    print(f"\n{'='*60}")
    print(f"SEKTION: {section_name.upper()}")
    print(f"{'='*60}")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Steg 1: Hämta möteslänkar grupperade per år
    year_meetings = get_meeting_links_by_year(session, index_url)

    if not year_meetings:
        print(f"[Varning] Inga möten hittades på indexsidan för {section_name}.")
        return

    total_downloaded = 0
    total_skipped = 0
    total_failed = 0

    # Steg 2: Iterera per år
    for year in sorted(year_meetings.keys(), reverse=True):
        meetings = year_meetings[year]
        print(f"\n--- År {year} ({len(meetings)} möten) ---")

        for meeting_url in meetings:
            print(f"\n  Möte: {meeting_url}")

            # Steg 3: Hämta PDF-länkar från mötessidan
            pdf_links = get_pdf_links_from_meeting_page(
                session, meeting_url, referer=index_url
            )

            if not pdf_links:
                print("    Inga PDF-filer hittades på denna sida.")
                continue

            print(f"    {len(pdf_links)} PDF(er) hittade")

            # Steg 4: Ladda ner varje PDF
            for pdf_url, link_text in pdf_links:
                filename = build_filename(pdf_url, link_text, year)
                dest_path = output_dir / filename

                already_existed = dest_path.exists()
                success = download_pdf(
                    session=session,
                    pdf_url=pdf_url,
                    dest_path=dest_path,
                    referer=meeting_url,
                )

                if success:
                    if already_existed:
                        total_skipped += 1
                    else:
                        total_downloaded += 1
                else:
                    total_failed += 1

                time.sleep(DELAY_BETWEEN_DOWNLOADS)

    print(f"\n[Sammanfattning – {section_name}]")
    print(f"  Nedladdade : {total_downloaded}")
    print(f"  Hoppade över: {total_skipped}")
    print(f"  Misslyckade: {total_failed}")


def main() -> None:
    print("Borås Stad PDF-skrapare")
    print("=" * 60)
    print(f"Sparar filer till: {DOWNLOAD_ROOT}")
    print(f"Filtrerade år: {', '.join(sorted(TARGET_YEARS))}")

    session = make_session()

    # Värm upp sessionen med en första förfrågan till startsidan
    print("\nInitierar session mot Borås Stads hemsida ...")
    warmup = safe_get(session, BASE_URL)
    if warmup:
        print("  Session initierad OK")
    else:
        print("  [Varning] Kunde inte initiera session, fortsätter ändå ...")

    time.sleep(1)

    for section_name, index_url in INDEX_PAGES.items():
        output_dir = DOWNLOAD_ROOT / section_name
        scrape_section(
            session=session,
            section_name=section_name,
            index_url=index_url,
            output_dir=output_dir,
        )
        time.sleep(2)  # Paus mellan sektioner

    print("\nKlart!")


if __name__ == "__main__":
    main()
