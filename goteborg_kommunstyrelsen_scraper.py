#!/usr/bin/env python3
"""
Göteborgs Stad – Kommunstyrelsen handlingar 2022–2026
======================================================
Laddar ner alla handlingar (kallelser, protokoll, ärendehandlingar) för
Kommunstyrelsen från Göteborgs stads hemsida.

Hur sidan fungerar (IBM WebSphere Portal + Lotus Domino):
  1. Filtreringssida med snNamnd=Kommunstyrelsen&snAr=YEAR → lista över möten
  2. Varje möte har en detaljsida med direkta PDF-länkar
  3. PDF-filerna ligger på www4.goteborg.se/.../SamrumPortal.nsf/.../$File/...pdf

Möteslistan pagineras via sitePage=0,1,2,...
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

# Portlet-URL för möteslistning (stabil per portlet-ID; navigationshashen
# kan ändras om portalen uppdateras – uppdatera då NAV_HASH nedan)
NAV_HASH = (
    "04_Sj9CPykssy0xPLMnMz0vMAfIjo8ziPR2NnA1NTAyNPEwDXAzMnH0DHZ0C_T19DY31"
    "w8EKfI2czQw9DIz83Z0t3QzMDAJdAgMDzA0M_I31o4jRb4ADOBoQpx-Pgij8xhfkhoaGOioqAgAGjmmX"
)
PORTLET_PATH = (
    "p0/IZ7_IA2C14412H5PD06CMQABQOIML3=CZ6_IA2C14412H5PD06CMQABQOIM13="
)

BASE_PORTAL_URL = (
    "https://goteborg.se/wps/portal/start/kommun-och-politik/"
    "handlingar-och-protokoll/namndhandlingar/valj-namnd-och-ar/"
    f"!ut/p/z1/{NAV_HASH}/"
)

LISTING_URL_TEMPLATE = (
    BASE_PORTAL_URL
    + PORTLET_PATH
    + "MEaction!filter==/"
    "?snNamnd=Kommunstyrelsen&snAr={year}&sitePage={page}"
)

TARGET_YEARS = [2022, 2023, 2024, 2025, 2026]
MAX_LISTING_PAGES = 10   # säkerhetsgräns för paginering
MAX_MEETING_PAGES = 3    # sidor per möte (nästan alltid 1)

DOWNLOAD_ROOT = Path(
    r"C:\Users\anan17\OneDrive - Sveriges Television\AI\Kommunprotokoll"
    r"\Goteborg\kommunstyrelsen"
)

REQUEST_TIMEOUT = 30
DELAY_BETWEEN_DOWNLOADS = 1.0
DELAY_BETWEEN_PAGES = 0.5

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


def extract_nav_base(url: str) -> str | None:
    """
    Extraherar nav-bas-URL:en (allt t.o.m. navigationshashen + /)
    ur en WebSphere Portal-URL.
    Ex: 'https://goteborg.se/.../!ut/p/z1/HASH/p0/...' → 'https://goteborg.se/.../!ut/p/z1/HASH/'
    """
    m = re.search(r'(https://goteborg\.se/wps/portal/.+?!ut/p/z\d+/[^/]+/)', url)
    return m.group(1) if m else None


def build_meeting_url(nav_base: str, rel_path: str) -> str:
    """Bygger absolut mötessidesURL från nav-bas och relativ länkpath."""
    return nav_base + rel_path.lstrip("/")


def extract_meeting_date(url: str) -> str | None:
    """Extraherar datum (YYYY-MM-DD) ur en mötesURL."""
    m = re.search(r'showmote!Kommunstyrelsen(\d{4}-\d{2}-\d{2})', url)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Möteslista
# ---------------------------------------------------------------------------

def get_meeting_urls_for_year(
    session: requests.Session,
    year: int,
) -> list[str]:
    """
    Hämtar alla mötessidors URL:er för ett givet år via listningssidan.
    Paginerar tills inga nya möten hittas.
    """
    all_urls: list[str] = []
    seen: set[str] = set()
    nav_base = BASE_PORTAL_URL  # fallback

    for page in range(0, MAX_LISTING_PAGES):
        listing_url = LISTING_URL_TEMPLATE.format(year=year, page=page)
        resp = safe_get(session, listing_url)
        if resp is None:
            print(f"  Kan inte hamta listningssida (ar={year}, page={page})")
            break

        # Uppdatera nav_base om portalen omdirigerade
        detected = extract_nav_base(resp.url)
        if detected:
            nav_base = detected

        html = resp.text

        # Extrahera relativa möteslänkar (innehåller "showmote!Kommunstyrelsen")
        raw_links = re.findall(
            r'href=["\']([^"\']*showmote!Kommunstyrelsen[^"\']*)["\']',
            html,
            re.IGNORECASE,
        )

        new_count = 0
        for rel in raw_links:
            abs_url = build_meeting_url(nav_base, rel)
            if abs_url not in seen:
                seen.add(abs_url)
                all_urls.append(abs_url)
                new_count += 1

        print(f"  Ar {year} sida {page}: {new_count} nya moten")

        if new_count == 0:
            break  # inga fler sidor

        time.sleep(DELAY_BETWEEN_PAGES)

    return all_urls


# ---------------------------------------------------------------------------
# PDF-extrahering ur mötessida
# ---------------------------------------------------------------------------

def get_pdf_links_from_meeting(
    session: requests.Session,
    meeting_url: str,
    seen_urls: set[str],
) -> list[tuple[str, str]]:
    """
    Hämtar en mötessida och returnerar lista med (pdf_url, filnamn).
    PDFerna ligger på www4.goteborg.se/.../SamrumPortal.nsf/.../$File/...
    """
    resp = safe_get(session, meeting_url)
    if resp is None:
        return []

    session.headers["Referer"] = meeting_url
    html = resp.text

    raw_links = re.findall(
        r'href=["\']('
        r'https://www4\.goteborg\.se/[^"\']+\.pdf[^"\']*'
        r')["\']',
        html,
        re.IGNORECASE,
    )

    results: list[tuple[str, str]] = []
    for url in raw_links:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        # Extrahera filnamn: allt efter $File/ och innan ?
        if "/$File/" in url:
            filename = url.split("/$File/")[-1].split("?")[0]
        else:
            filename = url.split("/")[-1].split("?")[0]
        filename = urllib.parse.unquote(filename)
        results.append((url, filename))

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
        print(f"  [OK] {size_kb:.1f} kB")
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
    print("Göteborgs Stad – Kommunstyrelsen handlingar 2022–2026")
    print("=" * 60)
    print(f"Sparar filer till: {DOWNLOAD_ROOT}")
    print(f"Ar: {', '.join(str(y) for y in TARGET_YEARS)}")
    print(
        "\nOBS: Portalen visar typiskt 2023–2026 i arsvaljaren."
        "\n     2022 kan vara begransat tillgangligt."
    )

    session = make_session()

    grand_downloaded = grand_skipped = grand_failed = 0
    seen_pdf_urls: set[str] = set()

    for year in TARGET_YEARS:
        print(f"\n{'='*60}")
        print(f"AR {year}")
        print(f"{'='*60}")

        meeting_urls = get_meeting_urls_for_year(session, year)

        if not meeting_urls:
            print(f"  Inga moten hittades for ar {year}.")
            continue

        print(f"  Totalt {len(meeting_urls)} moten funna for {year}")

        year_downloaded = year_skipped = year_failed = 0

        for meeting_url in meeting_urls:
            meeting_date = extract_meeting_date(meeting_url) or f"{year}-xx-xx"
            print(f"\n  Mote {meeting_date}")

            pdf_links = get_pdf_links_from_meeting(session, meeting_url, seen_pdf_urls)

            if not pdf_links:
                print(f"    Inga PDF:er hittades.")
                time.sleep(DELAY_BETWEEN_PAGES)
                continue

            print(f"    {len(pdf_links)} PDF(er) hittade")

            # Spara i DOWNLOAD_ROOT/year/meeting_date/
            meeting_dir = DOWNLOAD_ROOT / str(year) / meeting_date
            meeting_dir.mkdir(parents=True, exist_ok=True)

            for pdf_url, raw_filename in pdf_links:
                dest_filename = sanitize_filename(raw_filename)
                if not dest_filename.lower().endswith(".pdf"):
                    dest_filename += ".pdf"
                dest_path = meeting_dir / dest_filename

                already_existed = dest_path.exists()
                success = download_pdf(session, pdf_url, dest_path, referer=meeting_url)

                if success:
                    if already_existed:
                        year_skipped += 1
                    else:
                        year_downloaded += 1
                else:
                    year_failed += 1

                time.sleep(DELAY_BETWEEN_DOWNLOADS)

            time.sleep(DELAY_BETWEEN_PAGES)

        print(f"\n  Ar {year} klart: {year_downloaded} nedladdade, "
              f"{year_skipped} hoppade, {year_failed} misslyckade")

        grand_downloaded += year_downloaded
        grand_skipped += year_skipped
        grand_failed += year_failed

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
