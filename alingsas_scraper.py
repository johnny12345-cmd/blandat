#!/usr/bin/env python3
"""
Alingsås kommun – Kommunstyrelsen & Kommunfullmäktige handlingar 2022–2026
==========================================================================
Laddar ner protokoll från Alingsås sammanträdesportal (MeetingPlus) och
slår ihop dem till så få PDF-filer som möjligt, max 100 MB per fil.

API-flöde:
  1. GET /api/v2.0/committees/{id}/meetings  → lista med möten + ID
  2. GET /api/v2.0/meetings/{id}/download/Protocol?downloadMode=download → PDF

Kommittés-ID:
  Kommunstyrelsen  = 898
  Kommunfullmäktige = 887
"""

import time
import re
from pathlib import Path

import requests
from pypdf import PdfWriter, PdfReader

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

API_BASE = "https://sammantradesportal.alingsas.se/api/v2.0"

TARGET_COMMITTEES = {
    "Kommunstyrelsen":   898,
    "Kommunfullmaktige": 887,
}

TARGET_YEARS = {2022, 2023, 2024, 2025, 2026}

DOWNLOAD_ROOT = Path(
    r"C:\Users\anan17\OneDrive - Sveriges Television\VALET 2026"
    r"\Workshop val\Kommunprotokoll\Alingsas"
)

MAX_PDF_BYTES = 100 * 1024 * 1024   # 100 MB

REQUEST_TIMEOUT = 60
DELAY_BETWEEN_DOWNLOADS = 1.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, */*",
    "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
}

# ---------------------------------------------------------------------------
# HTTP-hjälpare
# ---------------------------------------------------------------------------

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def safe_get(session: requests.Session, url: str, retries: int = 3,
             stream: bool = False, json_mode: bool = False):
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, stream=stream)
            resp.raise_for_status()
            return resp
        except requests.exceptions.Timeout:
            print(f"  [Timeout] Forsok {attempt}/{retries}: {url}")
        except requests.exceptions.HTTPError as exc:
            print(f"  [HTTP-fel] {exc.response.status_code}: {url}")
            return None
        except requests.exceptions.RequestException as exc:
            print(f"  [Natverksfel] Forsok {attempt}/{retries}: {exc}")
        if attempt < retries:
            time.sleep(2 ** attempt)
    return None


# ---------------------------------------------------------------------------
# API-funktioner
# ---------------------------------------------------------------------------

def fetch_meetings(session: requests.Session, committee_id: int) -> list[dict]:
    """Hämtar alla möten för en kommitté från API:t."""
    url = f"{API_BASE}/committees/{committee_id}/meetings"
    resp = safe_get(session, url)
    if resp is None:
        return []
    try:
        return resp.json()
    except Exception as exc:
        print(f"  [JSON-fel] {exc}")
        return []


def filter_meetings(meetings: list[dict], years: set[int]) -> list[dict]:
    """Filtrerar möten på målår och sorterar per datum."""
    result = []
    for m in meetings:
        date_label = m.get("DateLabel", "")
        try:
            year = int(date_label[:4])
        except (ValueError, TypeError):
            continue
        if year in years:
            result.append(m)
    result.sort(key=lambda m: m.get("DateLabel", ""))
    return result


# ---------------------------------------------------------------------------
# Nedladdning av enskild protokolls-PDF
# ---------------------------------------------------------------------------

def download_protocol(
    session: requests.Session,
    meeting: dict,
    dest_path: Path,
) -> bool:
    """Laddar ner protokolls-PDF för ett möte. Returnerar True vid lyckat."""
    if dest_path.exists() and dest_path.stat().st_size > 0:
        print(f"  [Hoppar] Finns redan: {dest_path.name}")
        return True

    meeting_id = meeting["Id"]
    date_label = meeting.get("DateLabel", "okant")
    url = f"{API_BASE}/meetings/{meeting_id}/download/Protocol?downloadMode=download"

    print(f"  Laddar ner {date_label} (ID {meeting_id}) → {dest_path.name}")
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with session.get(url, timeout=REQUEST_TIMEOUT, stream=True) as resp:
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            if "html" in content_type and "pdf" not in content_type.lower():
                print(f"    [Varning] Oväntat Content-Type: '{content_type}' – hoppar over")
                return False
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
        size_kb = dest_path.stat().st_size / 1024
        print(f"    [OK] {size_kb:.0f} kB")
        return True
    except requests.exceptions.Timeout:
        print(f"    [Timeout] {url}")
    except requests.exceptions.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else "?"
        if code == 500:
            print(f"    [Inget protokoll] HTTP 500 – mötet saknar protokoll")
        else:
            print(f"    [HTTP-fel] {code}: {url}")
    except OSError as exc:
        print(f"    [I/O-fel] {exc}")

    if dest_path.exists():
        dest_path.unlink()
    return False


# ---------------------------------------------------------------------------
# PDF-sammanslagning
# ---------------------------------------------------------------------------

def merge_pdfs(pdf_paths: list[Path], out_path: Path) -> int:
    """
    Slår ihop pdf_paths till out_path.
    Returnerar antal sidor totalt, eller 0 vid fel.
    """
    writer = PdfWriter()
    total_pages = 0
    for p in pdf_paths:
        try:
            reader = PdfReader(str(p))
            for page in reader.pages:
                writer.add_page(page)
            total_pages += len(reader.pages)
        except Exception as exc:
            print(f"    [PDF-varning] Kunde inte lasa {p.name}: {exc}")

    if total_pages == 0:
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        writer.write(f)
    return total_pages


def merge_with_size_limit(
    pdf_paths: list[Path],
    out_dir: Path,
    base_name: str,
    max_bytes: int = MAX_PDF_BYTES,
) -> list[Path]:
    """
    Slår ihop pdf_paths till en eller flera filer (max max_bytes styck).
    Returnerar lista med skapade filer.
    """
    if not pdf_paths:
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    chunk: list[Path] = []
    chunk_size = 0
    chunk_idx = 1

    for p in pdf_paths:
        file_size = p.stat().st_size if p.exists() else 0

        # Om en enskild fil är >= maxgränsen, lägg den i en egen chunk
        if file_size >= max_bytes:
            # Töm nuvarande chunk först
            if chunk:
                out_path = out_dir / f"{base_name}_del{chunk_idx}.pdf"
                pages = merge_pdfs(chunk, out_path)
                if pages:
                    sz_mb = out_path.stat().st_size / 1024 / 1024
                    print(f"    Slog ihop {len(chunk)} fil(er) → {out_path.name} ({sz_mb:.1f} MB, {pages} sidor)")
                    created.append(out_path)
                chunk = []
                chunk_size = 0
                chunk_idx += 1
            # Kopiera/slå ihop den stora filen ensam
            out_path = out_dir / f"{base_name}_del{chunk_idx}.pdf"
            pages = merge_pdfs([p], out_path)
            if pages:
                sz_mb = out_path.stat().st_size / 1024 / 1024
                print(f"    Stor fil ensam → {out_path.name} ({sz_mb:.1f} MB, {pages} sidor)")
                created.append(out_path)
            chunk_idx += 1
            continue

        # Lägg till i chunk om den får plats
        if chunk and chunk_size + file_size > max_bytes:
            out_path = out_dir / f"{base_name}_del{chunk_idx}.pdf"
            pages = merge_pdfs(chunk, out_path)
            if pages:
                sz_mb = out_path.stat().st_size / 1024 / 1024
                print(f"    Slog ihop {len(chunk)} fil(er) → {out_path.name} ({sz_mb:.1f} MB, {pages} sidor)")
                created.append(out_path)
            chunk = []
            chunk_size = 0
            chunk_idx += 1

        chunk.append(p)
        chunk_size += file_size

    # Sista chunk
    if chunk:
        # Om bara en del → ingen del-suffix
        if chunk_idx == 1:
            out_path = out_dir / f"{base_name}.pdf"
        else:
            out_path = out_dir / f"{base_name}_del{chunk_idx}.pdf"
        pages = merge_pdfs(chunk, out_path)
        if pages:
            sz_mb = out_path.stat().st_size / 1024 / 1024
            print(f"    Slog ihop {len(chunk)} fil(er) → {out_path.name} ({sz_mb:.1f} MB, {pages} sidor)")
            created.append(out_path)

    return created


# ---------------------------------------------------------------------------
# Huvudflöde per kommitté
# ---------------------------------------------------------------------------

def process_committee(
    session: requests.Session,
    folder_name: str,
    committee_id: int,
) -> tuple[int, int, int]:
    """Laddar ner och slår ihop alla protokoll för en nämnd. Returnerar (dl, hoppade, fel)."""

    print(f"\n{'─' * 62}")
    print(f"  {folder_name}")
    print(f"{'─' * 62}")

    all_meetings = fetch_meetings(session, committee_id)
    meetings = filter_meetings(all_meetings, TARGET_YEARS)
    print(f"  {len(meetings)} möten hittade för åren {sorted(TARGET_YEARS)}")

    raw_dir = DOWNLOAD_ROOT / folder_name / "_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    downloaded = skipped = failed = 0

    # Dela upp möten per år
    meetings_by_year: dict[int, list[dict]] = {}
    for m in meetings:
        year = int(m["DateLabel"][:4])
        meetings_by_year.setdefault(year, []).append(m)

    for year in sorted(meetings_by_year):
        year_meetings = meetings_by_year[year]
        print(f"\n  --- {year} ({len(year_meetings)} möten) ---")

        downloaded_pdfs: list[Path] = []

        for meeting in year_meetings:
            date_label = meeting.get("DateLabel", "okant")
            meeting_id = meeting["Id"]
            dest_path = raw_dir / f"{date_label}_ID{meeting_id}.pdf"

            already_existed = dest_path.exists() and dest_path.stat().st_size > 0
            ok = download_protocol(session, meeting, dest_path)

            if ok:
                if already_existed:
                    skipped += 1
                else:
                    downloaded += 1
                downloaded_pdfs.append(dest_path)
            else:
                failed += 1

            time.sleep(DELAY_BETWEEN_DOWNLOADS)

        # Slå ihop alla protokoll för detta år
        if downloaded_pdfs:
            merged_dir = DOWNLOAD_ROOT / folder_name
            base_name = f"{folder_name}_{year}_protokoll"
            print(f"\n  Slar ihop {len(downloaded_pdfs)} PDF(er) for {year}...")
            created = merge_with_size_limit(downloaded_pdfs, merged_dir, base_name)
            if not created:
                print(f"  [Varning] Inga sammanslagna filer skapades for {year}")

    return downloaded, skipped, failed


# ---------------------------------------------------------------------------
# Ingångspunkt
# ---------------------------------------------------------------------------

def main() -> None:
    print("Alingsas kommun – Kommunstyrelsen & Kommunfullmaktige 2022–2026")
    print("=" * 62)
    print(f"Sparar filer till: {DOWNLOAD_ROOT}")

    session = make_session()

    grand_dl = grand_sk = grand_fa = 0

    for folder, cid in TARGET_COMMITTEES.items():
        dl, sk, fa = process_committee(session, folder, cid)
        grand_dl += dl
        grand_sk += sk
        grand_fa += fa
        time.sleep(1)

    print(f"\n{'=' * 62}")
    print("TOTALT")
    print(f"{'=' * 62}")
    print(f"  Nedladdade  : {grand_dl}")
    print(f"  Hoppade over: {grand_sk}")
    print(f"  Misslyckade : {grand_fa}")
    print("\nKlart!")


if __name__ == "__main__":
    main()
