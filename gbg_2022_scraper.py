#!/usr/bin/env python3
"""
Göteborgs stad – Kommunstyrelsen & Kommunfullmäktige handlingar 2022
=====================================================================
Laddar ner alla handlingar (kallelser, protokoll, ärendehandlingar) från
arkivsektionen "Äldre nämndhandlingar 2022–2020" på Göteborgs stads portal.

Hur sidan fungerar (IBM WebSphere Portal + Lotus Domino):
  1. Filtreringssida med snNamnd=X&snAr=2022&sitePage=N → lista över möten
  2. Varje möte har en detaljsida med direkta PDF-länkar
  3. PDF-filerna ligger på www4.goteborg.se/.../SamrumPortalArkiv_*.nsf/.../$File/...pdf

Notering: Kommunstyrelsen och Kommunfullmäktige har olika URL-mönster för möten.
  - Kommunstyrelsen:    href innehåller "showmote!Kommunstyrelsen{DATUM}"
  - Kommunfullmäktige: href innehåller portlet-state (MKf7qZkQFB=G*=)
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

# Navigations-hash ur den URL användaren angav (stabil så länge portlet är oförändrad)
NAV_HASH = (
    "04_Sj9CPykssy0xPLMnMz0vMAfIjo8ziLdwt3D2cnQ2MAkLcXQzMQr2DTY1MPbxMzIz0w8"
    "EKfI2czQw9DIz83Z0t3QzMDAJdAgMDzA0M_I31o4jRb4ADOBoQpx-Pgij8xhfkhoaGOioqAgDUGmLp"
)

PORTAL_BASE = (
    "https://goteborg.se/wps/portal/start/kommun-och-politik/"
    "handlingar-och-protokoll/namndhandlingar/aldre-namndhandlingar/"
    f"namndhandlingar-2022-till-2020/!ut/p/z1/{NAV_HASH}/"
)

PORTLET_PREFIX = "p0/IZ7_8G8GHCC02PTGD06UKS525HJ4M4=CZ6_8G8GHCC02PTGD06UKS525HJ462="

LISTING_URL_TEMPLATE = (
    PORTAL_BASE
    + PORTLET_PREFIX
    + "MEaction!filter==/"
    "?snNamnd={namnd}&snAr=2022&sitePage={page}"
)

TARGET_COMMITTEES = {
    "Kommunstyrelsen": "Kommunstyrelsen",
    "Kommunfullmaktige": "Kommunfullm%C3%A4ktige",  # URL-kodad ä
}

MAX_LISTING_PAGES = 5   # säkerhetsgräns för paginering

DOWNLOAD_ROOT = Path(
    r"C:\Users\anan17\OneDrive - Sveriges Television\VALET 2026"
    r"\Workshop val\Kommunprotokoll\GBG 2022"
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
}

# ---------------------------------------------------------------------------
# Hjälpfunktioner
# ---------------------------------------------------------------------------

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def safe_get(session: requests.Session, url: str, retries: int = 3,
             stream: bool = False):
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, stream=stream)
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
    """Extraherar nav-bas (allt t.o.m. /z1/HASH/) ur en WebSphere Portal-URL."""
    m = re.search(r'(https://goteborg\.se/wps/portal/.+?!ut/p/z\d+/[^/]+/)', url)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Möteslista – hämtar relativa möteslänkar + datum
# ---------------------------------------------------------------------------

def get_meeting_links(
    session: requests.Session,
    committee_folder: str,
    namnd_param: str,
) -> list[tuple[str, str]]:
    """
    Returnerar lista med (absolut_meeting_url, datum_YYYY-MM-DD).
    Paginerar tills inga nya möten hittas.
    """
    all_meetings: list[tuple[str, str]] = []
    seen_hrefs: set[str] = set()
    nav_base = PORTAL_BASE  # uppdateras om portalen omdirigerar

    for page in range(0, MAX_LISTING_PAGES):
        listing_url = LISTING_URL_TEMPLATE.format(namnd=namnd_param, page=page)
        resp = safe_get(session, listing_url)
        if resp is None:
            break

        # Uppdatera nav_base om vi fick en omdirigering
        detected = extract_nav_base(resp.url)
        if detected:
            nav_base = detected

        soup = BeautifulSoup(resp.text, "html.parser")

        new_count = 0
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]

            # Mötes-hrefs för Kommunstyrelsen: innehåller "showmote!Kommunstyrelsen"
            # Mötes-hrefs för Kommunfullmäktige: portlet-state-mönster (p0/IZ7_...=MKf7...)
            is_meeting_link = (
                "showmote!" in href
                or re.search(r'p0/IZ7_[^=]+=CZ6_[^=]+=M[A-Za-z0-9_]+=[A-Z]+=/', href)
            )
            if not is_meeting_link:
                continue

            # Hoppa över listning/filter-hrefs
            if "MEaction!filter" in href or "snNamnd" in href:
                continue

            if href in seen_hrefs:
                continue
            seen_hrefs.add(href)

            # Bygg absolut URL
            abs_url = nav_base + href.lstrip("/")

            # Extrahera datum ur href eller länktext
            date = _extract_date(href, a_tag.get_text(" ", strip=True))
            all_meetings.append((abs_url, date or "okant-datum"))
            new_count += 1

        print(f"  Sida {page}: {new_count} nya moten ({committee_folder})")
        if new_count == 0:
            break
        time.sleep(DELAY_BETWEEN_PAGES)

    return all_meetings


def _extract_date(href: str, link_text: str) -> str | None:
    """Försöker hitta YYYY-MM-DD i href eller länktext."""
    m = re.search(r'(\d{4}-\d{2}-\d{2})', href)
    if m:
        return m.group(1)
    m = re.search(r'(\d{4}-\d{2}-\d{2})', link_text)
    if m:
        return m.group(1)
    # Datum som "21 december 2022" → konvertera
    swedish_months = {
        "januari": "01", "februari": "02", "mars": "03", "april": "04",
        "maj": "05", "juni": "06", "juli": "07", "augusti": "08",
        "september": "09", "oktober": "10", "november": "11", "december": "12",
    }
    m = re.search(
        r'(\d{1,2})\s+(' + "|".join(swedish_months) + r')\s+(\d{4})',
        link_text, re.IGNORECASE
    )
    if m:
        day = m.group(1).zfill(2)
        month = swedish_months[m.group(2).lower()]
        year = m.group(3)
        return f"{year}-{month}-{day}"
    return None


# ---------------------------------------------------------------------------
# PDF-extrahering ur mötessida
# ---------------------------------------------------------------------------

def get_pdf_links(
    session: requests.Session,
    meeting_url: str,
    seen_urls: set[str],
) -> list[tuple[str, str]]:
    """
    Hämtar en mötessida och returnerar lista med (pdf_url, filnamn).
    PDFerna ligger på www4.goteborg.se.
    """
    resp = safe_get(session, meeting_url)
    if resp is None:
        return []

    session.headers["Referer"] = meeting_url
    soup = BeautifulSoup(resp.text, "html.parser")

    results: list[tuple[str, str]] = []

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        # Matcha PDF-länkar på www4.goteborg.se
        if not re.search(r'https?://www4\.goteborg\.se/', href, re.IGNORECASE):
            continue
        if not re.search(r'\.pdf', href, re.IGNORECASE):
            continue

        # Gör om http → https
        url = re.sub(r'^http://', 'https://', href)

        if url in seen_urls:
            continue
        seen_urls.add(url)

        # Extrahera filnamn efter $File/
        if "/$File/" in url:
            raw_name = url.split("/$File/")[-1].split("?")[0]
        else:
            raw_name = url.split("/")[-1].split("?")[0]

        filename = urllib.parse.unquote(raw_name)
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
        print(f"  [Hoppar] Finns redan: {dest_path.name}")
        return True

    session.headers["Referer"] = referer
    print(f"  Laddar ner → {dest_path.name}")

    try:
        with session.get(pdf_url, timeout=REQUEST_TIMEOUT, stream=True) as resp:
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            if "html" in content_type and "pdf" not in content_type:
                print(f"  [Varning] Oväntat Content-Type: '{content_type}'")
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

def process_committee(
    session: requests.Session,
    committee_folder: str,
    namnd_param: str,
) -> tuple[int, int, int]:
    """Laddar ner alla handlingar för en nämnd. Returnerar (dl, hoppade, fel)."""

    print(f"\n{'─' * 62}")
    print(f"  {committee_folder}")
    print(f"{'─' * 62}")

    meetings = get_meeting_links(session, committee_folder, namnd_param)
    print(f"  Totalt {len(meetings)} möten funna")

    downloaded = skipped = failed = 0
    seen_pdf_urls: set[str] = set()

    for meeting_url, meeting_date in meetings:
        print(f"\n  Möte {meeting_date}")

        pdf_links = get_pdf_links(session, meeting_url, seen_pdf_urls)

        if not pdf_links:
            print("    Inga PDF:er hittades.")
            time.sleep(DELAY_BETWEEN_PAGES)
            continue

        print(f"    {len(pdf_links)} PDF(er) hittade")

        # Mapp: DOWNLOAD_ROOT / committee_folder / YYYY-MM-DD /
        meeting_dir = DOWNLOAD_ROOT / committee_folder / meeting_date
        meeting_dir.mkdir(parents=True, exist_ok=True)

        for pdf_url, raw_filename in pdf_links:
            dest_filename = sanitize_filename(raw_filename)
            if not dest_filename.lower().endswith(".pdf"):
                dest_filename += ".pdf"
            dest_path = meeting_dir / dest_filename

            already_existed = dest_path.exists()
            ok = download_pdf(session, pdf_url, dest_path, referer=meeting_url)

            if ok:
                if already_existed:
                    skipped += 1
                else:
                    downloaded += 1
            else:
                failed += 1

            time.sleep(DELAY_BETWEEN_DOWNLOADS)

        time.sleep(DELAY_BETWEEN_PAGES)

    return downloaded, skipped, failed


def main() -> None:
    print("Göteborgs stad – Kommunstyrelsen & Kommunfullmäktige 2022")
    print("=" * 62)
    print(f"Sparar filer till: {DOWNLOAD_ROOT}")

    session = make_session()

    grand_dl = grand_sk = grand_fa = 0

    for folder, param in TARGET_COMMITTEES.items():
        dl, sk, fa = process_committee(session, folder, param)
        grand_dl += dl
        grand_sk += sk
        grand_fa += fa
        time.sleep(1)

    print(f"\n{'=' * 62}")
    print("TOTALT")
    print(f"{'=' * 62}")
    print(f"  Nedladdade  : {grand_dl}")
    print(f"  Hoppade över: {grand_sk}")
    print(f"  Misslyckade : {grand_fa}")
    print("\nKlart!")


if __name__ == "__main__":
    main()
