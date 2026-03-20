#!/usr/bin/env python3
"""
boras_scraper.py – Ladda hem samtliga protokoll, kallelser och
handlingar/bilagor från Borås Stads Sammanträdesportal
(diarier.boras.se) för alla nämnder, 2022–2026.

Systemet är Ciceron Sökning (Visma/twoday), identiskt med
diariet.gullspang.se. API: JSON-RPC 2.0 POST till /json.
"""

import os
import re
import json
import time
import base64
import requests
from pathlib import Path
from pypdf import PdfWriter, PdfReader

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

BASE_URL     = "https://diarier.boras.se"
RPC_URL      = f"{BASE_URL}/json"
DOWNLOAD_URL = f"{BASE_URL}/download/document"

OUTPUT_DIR = Path("Borås_handlingar")
TEMP_DIR   = OUTPUT_DIR / "_temp"

DATE_FROM = "2022-01-01"
DATE_TO   = "2026-12-31"

DOC_TYPE_MEETING = 1   # Möten (innehåller protokoll, kallelser och handlingar)

MAX_MERGED_SIZE    = 100 * 1024 * 1024   # 100 MB per sammanslagen fil
COMPRESS_THRESHOLD = 150 * 1024 * 1024   # Komprimera om > 150 MB

ITEMS_PER_PAGE = 50
MAX_RETRIES    = 5
RETRY_DELAY    = 2      # sekunder (fördubblats vid varje nytt försök)
POLITE_DELAY   = 0.25   # sekunder mellan API-anrop

HEADERS = {
    "Content-Type": "application/json; charset=utf-8",
    "Accept":       "application/json, text/plain, */*",
    "User-Agent":   "Mozilla/5.0 (compatible; BorasScraper/1.0)",
    "Origin":       BASE_URL,
    "Referer":      f"{BASE_URL}/",
}


# ---------------------------------------------------------------------------
# Ciceron JSON-RPC-klient
# ---------------------------------------------------------------------------

class CiceronClient:
    def __init__(self):
        self.session    = requests.Session()
        self.session.headers.update(HEADERS)
        self.session_id = None
        self._rpc_id    = 0

    # ------------------------------------------------------------------ low-level

    def _rpc(self, method: str, params: dict | None = None) -> dict | None:
        self._rpc_id += 1
        payload = {
            "jsonrpc": "2.0",
            "method":  method,
            "params":  params or {},
            "id":      self._rpc_id,
        }
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.post(RPC_URL, json=payload, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                if "error" in data:
                    print(f"  [RPC-fel] {method}: {data['error']}")
                    return None
                return data.get("result")
            except requests.exceptions.RequestException as exc:
                wait = RETRY_DELAY * (2 ** (attempt - 1))
                print(f"  [Nätverksfel] {method} (försök {attempt}/{MAX_RETRIES}): {exc}")
                if attempt < MAX_RETRIES:
                    time.sleep(wait)
        return None

    # ------------------------------------------------------------------ API-metoder

    def init_session(self) -> str | None:
        """CiceronsokServer:Test – returnerar session_id."""
        result = self._rpc("CiceronsokServer:Test")
        if result and "session_id" in result:
            self.session_id = result["session_id"]
            print(f"  Session initierad: {self.session_id}")
            return self.session_id
        print("  [FEL] Kunde inte initiera Ciceron-session.")
        return None

    def read_diaries(self) -> list[dict]:
        """CiceronsokServer:ReadDiaries – returnerar alla diarium (nämnder)."""
        result = self._rpc("CiceronsokServer:ReadDiaries", {"session_id": self.session_id})
        return result.get("diaries", []) if result else []

    def search(
        self,
        search_id:  str,
        doctype:    int,
        diary:      str = "",
        from_date:  str = "",
        to_date:    str = "",
    ) -> dict | None:
        """
        CiceronsokServer:Search – NB: param-fältet måste vara en
        JSON-serialiserad sträng, inte ett objekt.
        """
        param_dict = {
            "doctype":   doctype,
            "diary":     diary,
            "from_date": from_date,
            "to_date":   to_date,
        }
        return self._rpc("CiceronsokServer:Search", {
            "session_id": self.session_id,
            "search_id":  search_id,
            "param":      json.dumps(param_dict),   # <-- måste vara sträng
        })

    def read_items(self, search_id: str, offset: int, limit: int) -> list[dict]:
        """CiceronsokServer:ReadItems – paginerad lista med träffar."""
        result = self._rpc("CiceronsokServer:ReadItems", {
            "session_id": self.session_id,
            "search_id":  search_id,
            "offset":     offset,
            "limit":      limit,
        })
        return result.get("items", []) if result else []

    def read_object_details(self, search_id: str, item_id: str) -> dict | None:
        """CiceronsokServer:ReadObjectDetails – full info om ett möte."""
        result = self._rpc("CiceronsokServer:ReadObjectDetails", {
            "session_id": self.session_id,
            "search_id":  search_id,
            "id":         item_id,
        })
        return result if result else None

    def read_arende_files(
        self,
        instans:    str,
        mote_dat:   str,
        case_id:    str,
        diary_name: str,
        doctype:    int,
    ) -> list[dict]:
        """
        CiceronsokServer:ReadArendeFiles – dokument/bilagor för ett ärende
        i ett möte.
        """
        result = self._rpc("CiceronsokServer:ReadArendeFiles", {
            "session_id": self.session_id,
            "instans":    instans,
            "mote_dat":   mote_dat,
            "case_id":    case_id,
            "diary_name": diary_name,
            "doctype":    doctype,
        })
        return result.get("files", []) if result else []

    def download_document(
        self,
        doc_id:      str,
        filename_b64: str,
        dest_path:   Path,
    ) -> bool:
        """
        GET /download/document?filename=<b64>&id=<id>&session_id=<sid>
        Returnerar True om filen laddades ner (eller redan fanns).
        """
        if dest_path.exists() and dest_path.stat().st_size > 0:
            return True

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        url = (
            f"{DOWNLOAD_URL}"
            f"?filename={filename_b64}"
            f"&id={doc_id}"
            f"&session_id={self.session_id}"
        )
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.get(url, timeout=60, stream=True)
                resp.raise_for_status()
                with open(dest_path, "wb") as fh:
                    for chunk in resp.iter_content(8192):
                        if chunk:
                            fh.write(chunk)
                size_kb = dest_path.stat().st_size / 1024
                print(f"    [OK] {dest_path.name}  ({size_kb:.0f} kB)")
                return True
            except requests.exceptions.RequestException as exc:
                wait = RETRY_DELAY * (2 ** (attempt - 1))
                print(f"    [Fel] {dest_path.name} – {exc} (försök {attempt})")
                if dest_path.exists():
                    dest_path.unlink()
                if attempt < MAX_RETRIES:
                    time.sleep(wait)
        return False


# ---------------------------------------------------------------------------
# PDF-hjälpfunktioner
# ---------------------------------------------------------------------------

def merge_pdfs(pdf_paths: list[Path], output_path: Path) -> None:
    writer = PdfWriter()
    for p in pdf_paths:
        try:
            reader = PdfReader(str(p))
            for page in reader.pages:
                writer.add_page(page)
        except Exception as exc:
            print(f"  [Varning] Hoppar över skadat PDF: {p.name}: {exc}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as fh:
        writer.write(fh)


def compress_pdf(pdf_path: Path) -> None:
    """Minskar filstorleken med compress_content_streams() per sida."""
    print(f"  Komprimerar {pdf_path.name} …")
    try:
        reader = PdfReader(str(pdf_path))
        writer = PdfWriter()
        for page in reader.pages:
            page.compress_content_streams()
            writer.add_page(page)
        tmp = pdf_path.with_suffix(".tmp.pdf")
        with open(tmp, "wb") as fh:
            writer.write(fh)
        tmp.replace(pdf_path)
        size_mb = pdf_path.stat().st_size / (1024 * 1024)
        print(f"  Komprimerad → {size_mb:.1f} MB")
    except Exception as exc:
        print(f"  [Fel] Komprimering misslyckades: {exc}")


def merge_with_size_limit(
    pdf_paths: list[Path],
    base_name: str,
    output_dir: Path,
) -> None:
    """
    Slår ihop pdf_paths till filer ≤ MAX_MERGED_SIZE.
    Komprimerar färdiga filer > COMPRESS_THRESHOLD.
    """
    if not pdf_paths:
        return
    output_dir.mkdir(parents=True, exist_ok=True)

    chunk:      list[Path] = []
    chunk_size: int        = 0
    part:       int        = 1

    def _flush(ch: list[Path], p: int) -> None:
        suffix = f"_del{p}" if p > 1 else ""
        out = output_dir / f"{base_name}{suffix}.pdf"
        print(f"\n  Slår ihop {len(ch)} PDF(er) → {out.name}")
        merge_pdfs(ch, out)
        fsize = out.stat().st_size
        print(f"  Storlek: {fsize / (1024*1024):.1f} MB")
        if fsize > COMPRESS_THRESHOLD:
            compress_pdf(out)

    for pdf in pdf_paths:
        fsize = pdf.stat().st_size if pdf.exists() else 0
        if chunk and chunk_size + fsize > MAX_MERGED_SIZE:
            _flush(chunk, part)
            part += 1
            chunk      = []
            chunk_size = 0
        chunk.append(pdf)
        chunk_size += fsize

    if chunk:
        _flush(chunk, part)


# ---------------------------------------------------------------------------
# Dokumenthämtning per möte
# ---------------------------------------------------------------------------

def safe_filename(name: str, max_len: int = 180) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r'\s+', " ", name).strip()
    return name[:max_len]


def fetch_meeting_documents(
    client:       CiceronClient,
    search_id:    str,
    item_index:   int,
    item:         dict,
    meeting_dir:  Path,
) -> list[Path]:
    """
    Laddar ner alla dokument för ett möte:
      1. Protokoll/kallelse från details['documents']
      2. Handlingar/bilagor via ReadArendeFiles för varje ärendepost
    Returnerar lista med nedladdade PDF-sökvägar.
    """
    item_id    = str(item.get("id", ""))
    item_title = item.get("title", f"möte_{item_index}")
    date_str   = str(item.get("date", ""))[:10]
    instans    = str(item.get("instans", ""))
    diary_name = str(item.get("diary_name", ""))

    safe_title = safe_filename(f"{date_str}_{item_title}")
    meeting_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n  [{item_index}] {safe_title}")

    details = client.read_object_details(search_id, item_id)
    time.sleep(POLITE_DELAY)
    if not details:
        print("    Inga detaljer.")
        return []

    downloaded: list[Path] = []

    # -- 1. Protokoll / kallelse -------------------------------------------
    for doc in details.get("documents", []):
        doc_id       = str(doc.get("id", ""))
        filename_b64 = doc.get("filename_b64") or doc.get("filename", "")
        doc_title    = doc.get("title") or doc.get("name") or f"dok_{doc_id}"
        if not doc_id or not filename_b64:
            continue

        dest = meeting_dir / safe_filename(f"{doc_title}.pdf")
        if not dest.suffix.lower() == ".pdf":
            dest = dest.with_suffix(".pdf")
        if client.download_document(doc_id, filename_b64, dest):
            downloaded.append(dest)
        time.sleep(POLITE_DELAY)

    # -- 2. Handlingar och bilagor per ärende ------------------------------
    for agenda_item in details.get("items", []):
        case_id  = str(agenda_item.get("id", ""))
        case_no  = str(agenda_item.get("number", ""))
        mote_dat = str(agenda_item.get("mote_dat", date_str))
        doctype  = int(agenda_item.get("doctype", 4))

        if not case_id:
            continue

        files = client.read_arende_files(instans, mote_dat, case_id, diary_name, doctype)
        time.sleep(POLITE_DELAY)

        for f in files:
            if not f.get("exists", False):
                continue
            doc_id       = str(f.get("id", ""))
            filename_b64 = f.get("filename_b64") or f.get("filename", "")
            f_title      = f.get("title") or f.get("name") or f"bilaga_{doc_id}"
            if not doc_id or not filename_b64:
                continue

            dest = meeting_dir / safe_filename(f"{case_no}_{f_title}.pdf")
            if not dest.suffix.lower() == ".pdf":
                dest = dest.with_suffix(".pdf")
            if client.download_document(doc_id, filename_b64, dest):
                downloaded.append(dest)
            time.sleep(POLITE_DELAY)

    return downloaded


# ---------------------------------------------------------------------------
# Huvudflöde
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 65)
    print("Borås Stad – Ciceron Sammanträdesportal-skrapare")
    print(f"Källa:  {BASE_URL}")
    print(f"Period: {DATE_FROM} – {DATE_TO}")
    print(f"Utdata: {OUTPUT_DIR.resolve()}")
    print("=" * 65)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    client = CiceronClient()

    # -- 1. Initiera session -----------------------------------------------
    print("\n[1/4] Initierar Ciceron-session …")
    if not client.init_session():
        print("Avbryter – ingen session.")
        return

    # -- 2. Hämta alla diarium (nämnder) -----------------------------------
    print("\n[2/4] Hämtar diarium …")
    diaries = client.read_diaries()
    if not diaries:
        print("Inga diarium returnerades – kontrollera anslutning.")
        return

    print(f"  {len(diaries)} diarium hittade:")
    for d in diaries:
        print(f"    {d.get('id','?'):8s}  {d.get('name','?')}")

    # -- 3. Sök och ladda ner per diarium ----------------------------------
    print("\n[3/4] Söker och laddar ner handlingar …")

    all_pdfs_by_diary: dict[str, list[Path]] = {}

    for diary in diaries:
        diary_id   = str(diary.get("id", ""))
        diary_name = diary.get("name", diary_id)
        safe_diary = safe_filename(diary_name)

        print(f"\n{'─'*60}")
        print(f"DIARIUM: {diary_name}  (id={diary_id})")
        print(f"{'─'*60}")

        search_id = f"boras_{diary_id}"

        result = client.search(
            search_id  = search_id,
            doctype    = DOC_TYPE_MEETING,
            diary      = diary_id,
            from_date  = DATE_FROM,
            to_date    = DATE_TO,
        )
        time.sleep(POLITE_DELAY)

        if not result:
            print("  Sökning misslyckades, hoppar över.")
            continue

        total_hits = int(result.get("total", 0))
        print(f"  Träffar: {total_hits}")
        if total_hits == 0:
            continue

        diary_pdfs: list[Path] = []

        for offset in range(0, total_hits, ITEMS_PER_PAGE):
            items = client.read_items(search_id, offset, ITEMS_PER_PAGE)
            time.sleep(POLITE_DELAY)

            for idx, item in enumerate(items, start=offset + 1):
                date_str   = str(item.get("date", "okänt"))[:10]
                meeting_dir = (
                    OUTPUT_DIR
                    / safe_diary
                    / date_str
                )
                pdfs = fetch_meeting_documents(
                    client      = client,
                    search_id   = search_id,
                    item_index  = idx,
                    item        = item,
                    meeting_dir = meeting_dir,
                )
                diary_pdfs.extend(pdfs)

        all_pdfs_by_diary[safe_diary] = diary_pdfs
        print(f"\n  {len(diary_pdfs)} PDF(er) hämtade för {diary_name}.")

    # -- 4. Slå ihop till filer ≤ 100 MB ----------------------------------
    print("\n[4/4] Slår ihop och komprimerar …")

    for diary_safe, pdfs in all_pdfs_by_diary.items():
        if not pdfs:
            continue
        # Filtrera bort ev. saknade filer
        existing = [p for p in pdfs if p.exists() and p.stat().st_size > 0]
        if not existing:
            continue

        merged_dir = OUTPUT_DIR / "_sammanslagen"
        base_name  = f"Borås_{diary_safe}_{DATE_FROM[:4]}-{DATE_TO[:4]}"
        merge_with_size_limit(existing, base_name, merged_dir)

    print("\nKlart!")
    print(f"Handlingar sparade i: {OUTPUT_DIR.resolve()}")
    print(f"Sammanslagna filer i: {(OUTPUT_DIR / '_sammanslagen').resolve()}")


if __name__ == "__main__":
    main()
