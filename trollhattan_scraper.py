#!/usr/bin/env python3
"""
Trollhättans stad – Kommunstyrelsen & Kommunfullmäktige handlingar 2022–2026
=============================================================================
Laddar ner handlingar från Trollhättans webbdiarium (Evolution.Internet / Sokigo).

API-flöde:
  1. GET /api/units                          → lista över diarier (code, name)
  2. GET /api/decisionAuthoritys/{unitCode}  → beslutsinstanser för ett diarium
  3. GET /api/meetings/{authorityCode}       → alla möten för en beslutsinstans
  4. GET /api/meetings/notices/{meetingId}   → kallelse-/underlagshandlingar
     GET /api/meetings/decisions/{meetingId} → beslutshandlingar
  5. GET /api/documents/{documentId}.pdf     → ladda ner som PDF
     GET /api/documents/{documentId}.bin     → ladda ner i originalformat

Valda beslutsinstanser:
  - Kommunfullmäktige  (code 584, unit KS)
  - Kommunstyrelsen    (code 599, unit KS)
"""

import os
import re
import time
from datetime import datetime
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

BASE_URL = "https://webbdiariet.trollhattan.se/api"

# Beslutsinstanser att ladda ner (name → authority code)
TARGET_AUTHORITIES = {
    "Kommunfullmaktige": "584",
    "Kommunstyrelsen": "599",
}

TARGET_YEARS = range(2022, 2027)  # 2022–2026 inkl.

DOWNLOAD_ROOT = Path(
    r"C:\Users\anan17\OneDrive - Sveriges Television\AI\Kommunprotokoll"
    r"\Trollhattan"
)

# Nedladdningsformat: "pdf" konverterar allt till PDF, "bin" ger originalformat
DOWNLOAD_FORMAT = "pdf"

REQUEST_TIMEOUT = 30
DELAY_BETWEEN_DOWNLOADS = 0.8
DELAY_BETWEEN_MEETINGS = 0.3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
    "Referer": "https://webbdiariet.trollhattan.se/",
}

# ---------------------------------------------------------------------------
# Hjälpfunktioner
# ---------------------------------------------------------------------------

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def safe_get(session: requests.Session, url: str, retries: int = 3, stream: bool = False):
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, stream=stream)
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
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r'\s+', " ", name).strip()
    if len(name) > 180:
        name = name[:180]
    return name


def within_years(date_str: str, years) -> bool:
    """Returnerar True om datumet (ISO-sträng) tillhör ett av de önskade åren."""
    try:
        year = datetime.fromisoformat(date_str).year
        return year in years
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# API-anrop
# ---------------------------------------------------------------------------

def get_meetings(session: requests.Session, authority_code: str) -> list[dict]:
    url = f"{BASE_URL}/meetings/{authority_code}"
    resp = safe_get(session, url)
    if resp is None:
        return []
    return resp.json().get("data", [])


def get_documents_for_meeting(session: requests.Session, meeting_id: str) -> list[dict]:
    """Hämtar både kallelse-underlag (notices) och beslut (decisions)."""
    docs: list[dict] = []

    for endpoint in ["notices", "decisions"]:
        url = f"{BASE_URL}/meetings/{endpoint}/{meeting_id}"
        resp = safe_get(session, url)
        if resp is not None:
            data = resp.json().get("data", [])
            for d in data:
                d["_source"] = endpoint  # märk källan (notices/decisions)
            docs.extend(data)
        time.sleep(0.2)

    return docs


def download_document(
    session: requests.Session,
    document_id: str,
    dest_path: Path,
    fmt: str = "pdf",
) -> bool:
    if dest_path.exists():
        print(f"    [Hoppar] Finns redan: {dest_path.name}")
        return True

    url = f"{BASE_URL}/documents/{document_id}.{fmt}"
    print(f"    ↓ {dest_path.name}")

    try:
        with session.get(url, timeout=REQUEST_TIMEOUT, stream=True) as resp:
            resp.raise_for_status()
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        size_kb = dest_path.stat().st_size / 1024
        print(f"    [OK] {size_kb:.1f} kB")
        return True
    except requests.exceptions.Timeout:
        print(f"    [Timeout] {url}")
    except requests.exceptions.HTTPError as exc:
        print(f"    [HTTP-fel] {exc}")
    except OSError as exc:
        print(f"    [I/O-fel] {exc}")

    if dest_path.exists():
        dest_path.unlink()
    return False


# ---------------------------------------------------------------------------
# Huvudflöde
# ---------------------------------------------------------------------------

def process_authority(
    session: requests.Session,
    authority_name: str,
    authority_code: str,
    years,
) -> tuple[int, int, int]:
    """Laddar ner alla handlingar för en beslutsinstans inom angivna år.
    Returnerar (nedladdade, hoppade, misslyckade)."""

    print(f"\n{'─' * 60}")
    print(f"  {authority_name}  (kod {authority_code})")
    print(f"{'─' * 60}")

    meetings = get_meetings(session, authority_code)
    meetings_in_range = [m for m in meetings if within_years(m.get("date", ""), years)]

    print(f"  Totalt {len(meetings)} möten, {len(meetings_in_range)} inom {min(years)}–{max(years)}")

    downloaded = skipped = failed = 0

    for meeting in meetings_in_range:
        meeting_id = meeting["id"]
        meeting_date = meeting["date"][:10]  # YYYY-MM-DD

        print(f"\n  Möte {meeting_date} (id {meeting_id[:8]}…)")

        docs = get_documents_for_meeting(session, meeting_id)

        if not docs:
            print("    Inga dokument hittades.")
            time.sleep(DELAY_BETWEEN_MEETINGS)
            continue

        print(f"    {len(docs)} dokument funna")

        # Mapp: DOWNLOAD_ROOT / authority_name / YYYY / YYYY-MM-DD /
        year = meeting_date[:4]
        meeting_dir = DOWNLOAD_ROOT / authority_name / year / meeting_date
        meeting_dir.mkdir(parents=True, exist_ok=True)

        seen_doc_ids: set[str] = set()

        for doc in docs:
            doc_id = doc.get("documentId")
            if not doc_id or doc_id in seen_doc_ids:
                continue
            seen_doc_ids.add(doc_id)

            # Hoppa över dokument utan filändelse (hemliga/ej publicerade)
            if not doc.get("fileExtension"):
                continue

            # Bygg filnamn: agendaordning + hid + beskrivning
            agenda_order = doc.get("agendaOrder", "")
            hid = doc.get("hid", "")
            description = doc.get("description", "dokument")
            source = doc.get("_source", "")

            # Prefix för källtyp
            src_prefix = "kallelse" if source == "notices" else "beslut"
            raw_name = f"{src_prefix}_§{agenda_order}_{hid}_{description}.{DOWNLOAD_FORMAT}"
            filename = sanitize_filename(raw_name)

            dest = meeting_dir / filename

            already_existed = dest.exists()
            ok = download_document(session, doc_id, dest, DOWNLOAD_FORMAT)

            if ok:
                if already_existed:
                    skipped += 1
                else:
                    downloaded += 1
            else:
                failed += 1

            time.sleep(DELAY_BETWEEN_DOWNLOADS)

        time.sleep(DELAY_BETWEEN_MEETINGS)

    return downloaded, skipped, failed


def main() -> None:
    print("Trollhättans stad – Kommunstyrelsen & Kommunfullmäktige 2022–2026")
    print("=" * 66)
    print(f"Sparar filer till: {DOWNLOAD_ROOT}")
    print(f"Beslutsinstanser : {', '.join(TARGET_AUTHORITIES)}")
    print(f"År               : {min(TARGET_YEARS)}–{max(TARGET_YEARS)}")

    session = make_session()

    grand_downloaded = grand_skipped = grand_failed = 0

    for authority_name, authority_code in TARGET_AUTHORITIES.items():
        dl, sk, fa = process_authority(
            session, authority_name, authority_code, TARGET_YEARS
        )
        grand_downloaded += dl
        grand_skipped += sk
        grand_failed += fa
        time.sleep(1)

    print(f"\n{'=' * 66}")
    print("TOTALT")
    print(f"{'=' * 66}")
    print(f"  Nedladdade  : {grand_downloaded}")
    print(f"  Hoppade över: {grand_skipped}")
    print(f"  Misslyckade : {grand_failed}")
    print("\nKlart!")


if __name__ == "__main__":
    main()
