import asyncio
import functools
import os
import re
import gspread
from google.oauth2.service_account import Credentials
from config import (
    TRANSACTION_SHEET_NAME,
    MASTER_SHEET_NAME,
    today_str,
    get_dashboard_sheet_name,
)


def _to_async(fn):
    """Decorator: jalankan fungsi sync di thread terpisah agar tidak memblokir event loop."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)
    return wrapper

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

_client = None
_spreadsheet = None


def _get_client():
    global _client
    if _client is None:
        # Prioritas 1: GOOGLE_CREDENTIALS_JSON (isi JSON sebagai env var, untuk server/cloud)
        # Prioritas 2: GOOGLE_CREDENTIALS_PATH (path ke file, untuk lokal)
        json_str = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        if json_str:
            import json
            from google.oauth2.service_account import Credentials as SACredentials
            info = json.loads(json_str)
            creds = SACredentials.from_service_account_info(info, scopes=SCOPES)
        else:
            creds = Credentials.from_service_account_file(
                os.environ["GOOGLE_CREDENTIALS_PATH"], scopes=SCOPES
            )
        _client = gspread.authorize(creds)
    return _client


def _get_sheet(sheet_name: str):
    global _spreadsheet
    client = _get_client()
    if _spreadsheet is None:
        _spreadsheet = client.open_by_key(os.environ["SPREADSHEET_ID"])
    return _spreadsheet.worksheet(sheet_name)


def _parse_rupiah(value: str) -> float:
    if not value:
        return 0.0
    clean = re.sub(r"[Rp\s,]", "", str(value))
    try:
        return float(clean)
    except ValueError:
        return 0.0


_TIPE_ALIASES = {
    "pemasukan": "pemasukan", "income": "pemasukan", "masuk": "pemasukan",
    "pengeluaran": "pengeluaran", "expense": "pengeluaran", "keluar": "pengeluaran",
    "tabungan": "tabungan", "saving": "tabungan",
    "passthrough": "passthrough", "pass-through": "passthrough", "neraca": "passthrough",
    "transfer": "passthrough",
}


def get_master_categories() -> dict[str, str] | None:
    """Baca daftar kategori + tipe dari sheet Data Master.

    Cari kolom berjudul 'Kategori' dan 'Tipe' (case-insensitive) di baris header.
    Kembalikan {kategori: tipe} dengan tipe dinormalisasi, atau None bila gagal.
    """
    try:
        sheet = _get_sheet(MASTER_SHEET_NAME)
        rows = sheet.get_values()
        if not rows:
            return None
        header = [h.strip().lower() for h in rows[0]]
        try:
            i_kat = header.index("kategori")
        except ValueError:
            return None
        i_tipe = header.index("tipe") if "tipe" in header else None

        result: dict[str, str] = {}
        for row in rows[1:]:
            if len(row) <= i_kat:
                continue
            kat = row[i_kat].strip()
            if not kat:
                continue
            tipe_raw = row[i_tipe].strip().lower() if (i_tipe is not None and len(row) > i_tipe) else ""
            if tipe_raw in _TIPE_ALIASES:
                result[kat] = _TIPE_ALIASES[tipe_raw]
            else:
                # Tipe kosong/asing → tebak dari prefix (pos neraca vs pengeluaran)
                result[kat] = "passthrough" if kat.startswith(
                    ("[Transfer]", "[Kas RT]", "[Titipan]", "[Hutang]", "[Piutang]")
                ) else "pengeluaran"
        return result or None
    except Exception:
        return None


def get_last_saldo() -> float:
    sheet = _get_sheet(TRANSACTION_SHEET_NAME)
    all_values = sheet.col_values(7)
    for val in reversed(all_values):
        val_clean = val.strip()
        if val_clean and val_clean.lower() != "saldo":
            return _parse_rupiah(val_clean)
    return 0.0


_last_row = None  # cache baris kosong pertama, hindari full column scan tiap simpan


def _find_first_empty_row(sheet) -> int:
    global _last_row
    if _last_row is not None:
        return _last_row
    col_a = sheet.col_values(1)
    for i, val in enumerate(col_a, start=1):
        if i == 1:
            continue
        if not val.strip():
            _last_row = i
            return i
    _last_row = len(col_a) + 1
    return _last_row


def _write_row(sheet, tanggal, deskripsi, kategori, akun, debit, kredit, keterangan=""):
    """
    Tulis satu baris transaksi ke baris kosong pertama.
    Returns: (target_row, new_saldo)
    """
    global _last_row
    target_row = _find_first_empty_row(sheet)
    prev_row = target_row - 1

    row_data = [
        tanggal,
        deskripsi,
        kategori,
        akun,
        debit if debit != 0 else "",
        kredit if kredit != 0 else "",
    ]

    # Batch update: gabung 3 API call jadi 1
    sheet.batch_update([
        {"range": f"A{target_row}:F{target_row}", "values": [row_data]},
        {"range": f"H{target_row}", "values": [[keterangan]]},
        {"range": f"G{target_row}", "values": [[f"=G{prev_row}+E{target_row}-F{target_row}"]]},
    ], value_input_option="USER_ENTERED")

    # Baca saldo hasil formula
    saldo_raw = sheet.cell(target_row, 7).value
    _last_row = target_row + 1
    return target_row, _parse_rupiah(saldo_raw or "0")


@_to_async
def append_transaction(
    tanggal: str,
    deskripsi: str,
    kategori: str,
    akun: str,
    debit: float,
    kredit: float,
    keterangan: str = "",
) -> float:
    """Tambah satu baris transaksi. Returns saldo baru."""
    sheet = _get_sheet(TRANSACTION_SHEET_NAME)
    _, new_saldo = _write_row(sheet, tanggal, deskripsi, kategori, akun, debit, kredit, keterangan)
    return new_saldo


@_to_async
def append_transfer(
    tanggal: str,
    akun_asal: str,
    akun_tujuan: str,
    nominal: float,
    kode_unik: float = 0.0,
) -> float:
    """
    Catat transfer internal sebagai 2 baris:
    1. Kredit di akun asal (nominal + kode_unik jika ada)
    2. Debit di akun tujuan (nominal bersih)
    Deskripsi otomatis, kecuali tarik tunai (tujuan = Cash).
    Returns: saldo total setelah kedua baris.
    """
    sheet = _get_sheet(TRANSACTION_SHEET_NAME)
    kategori = "[Transfer] Internal"

    # Tentukan deskripsi
    if akun_tujuan.lower() == "cash":
        deskripsi_asal = "Tarik tunai"
        deskripsi_tujuan = "Tarik tunai"
    elif kode_unik > 0:
        deskripsi_asal = f"Kirim ke {akun_tujuan} (Flip)"
        deskripsi_tujuan = f"Terima dari {akun_asal} (Flip)"
    else:
        deskripsi_asal = f"Kirim ke {akun_tujuan}"
        deskripsi_tujuan = f"Terima dari {akun_asal}"

    total_keluar = nominal + kode_unik

    # Baris 1: kredit dari akun asal (termasuk kode unik)
    _write_row(sheet, tanggal, deskripsi_asal, kategori, akun_asal, 0, total_keluar)

    # Baris 2: debit ke akun tujuan (nominal bersih)
    _, new_saldo = _write_row(sheet, tanggal, deskripsi_tujuan, kategori, akun_tujuan, nominal, 0)

    return new_saldo


def _parse_row(row: list[str]) -> dict | None:
    """Parse satu baris transaksi menjadi dict. Return None jika baris kosong."""
    if not row or not row[0].strip():
        return None
    return {
        "tanggal": row[0] if len(row) > 0 else "",
        "deskripsi": row[1] if len(row) > 1 else "",
        "kategori": row[2] if len(row) > 2 else "",
        "akun": row[3] if len(row) > 3 else "",
        "debit": _parse_rupiah(row[4]) if len(row) > 4 else 0.0,
        "kredit": _parse_rupiah(row[5]) if len(row) > 5 else 0.0,
        "saldo": _parse_rupiah(row[6]) if len(row) > 6 else 0.0,
    }


@_to_async
def get_riwayat(n: int = 10, akun_list: list[str] | None = None) -> list[dict]:
    """Ambil n transaksi terakhir dari Transaction Log. Optional filter by akun.

    Jika akun_list diberikan, scan sheet dari bawah ke atas sampai
    mendapatkan n transaksi yang match dengan akun filter.
    """
    sheet = _get_sheet(TRANSACTION_SHEET_NAME)
    col_a = sheet.col_values(1)

    # Cari baris data terakhir
    last_row = 1
    for i, val in enumerate(col_a, start=1):
        if i == 1:
            continue
        if val.strip():
            last_row = i

    if last_row < 2:
        return []

    if akun_list:
        # Filter by akun: scan dari bawah ke atas (batch scan) sampai dapat n transaksi
        result = []
        batch_size = 50
        current_end = last_row

        while len(result) < n and current_end >= 2:
            current_start = max(2, current_end - batch_size + 1)
            rows = sheet.get_values(f"A{current_start}:G{current_end}")

            # Proses dari bawah (paling baru) ke atas dalam batch ini
            for row in reversed(rows):
                parsed = _parse_row(row)
                if parsed and parsed["akun"] in akun_list:
                    result.append(parsed)
                    if len(result) >= n:
                        break

            current_end = current_start - 1

        return result
    else:
        # Tanpa filter: ambil n baris terakhir (existing behavior)
        start_row = max(2, last_row - n + 1)
        rows = sheet.get_values(f"A{start_row}:G{last_row}")

        result = []
        for row in rows:
            parsed = _parse_row(row)
            if parsed:
                result.append(parsed)

        return result


@_to_async
def get_all_transactions() -> list[dict]:
    """Ambil seluruh baris transaksi (parsed) dari Transaction Log dalam satu batch."""
    sheet = _get_sheet(TRANSACTION_SHEET_NAME)
    col_a = sheet.col_values(1)
    last_row = 1
    for i, val in enumerate(col_a, start=1):
        if i == 1:
            continue
        if val.strip():
            last_row = i
    if last_row < 2:
        return []
    rows = sheet.get_values(f"A2:G{last_row}")
    result = []
    for row in rows:
        parsed = _parse_row(row)
        if parsed:
            result.append(parsed)
    return result


@_to_async
def get_transaksi_hari_ini(tanggal: str) -> list[dict]:
    """Ambil semua transaksi di tanggal tertentu."""
    sheet = _get_sheet(TRANSACTION_SHEET_NAME)
    rows = sheet.get_all_records()
    return [
        r for r in rows
        if str(r.get("Tanggal", "")).strip() == tanggal
        and str(r.get("Akun/Rekening", "")).strip()
    ]


@_to_async
def get_kas_rt_buku() -> float | None:
    """Baca saldo kas RT menurut buku bendahara dari dashboard bulan ini (sel M19).

    Kembalikan None bila sel kosong atau sheet tak terbaca.
    """
    try:
        sheet = _get_sheet(get_dashboard_sheet_name())
        val = sheet.cell(19, 13).value  # M19 (kolom M = 13, baris 19)
        if val is None or str(val).strip() == "":
            return None
        return _parse_rupiah(val)
    except Exception:
        return None


@_to_async
def get_hutang_from_dashboard() -> tuple[float | None, list[tuple[str, float]]]:
    """Baca blok hutang dari dashboard bulan ini (H23:M28).

    Layout: H=Pemberi, I=Pinjaman, J=Dibayar, K=Sisa, ... baris terakhir = TOTAL (K28).
    Return (total_sisa, [(pemberi, sisa), ...]). total_sisa None bila tak terbaca.
    """
    try:
        sheet = _get_sheet(get_dashboard_sheet_name())
        rows = sheet.get_values("H23:M28")
    except Exception:
        return None, []
    if not rows:
        return None, []

    def cell(r: int, c: int) -> str:
        return rows[r][c].strip() if r < len(rows) and c < len(rows[r]) else ""

    # Total sisa = kolom K (indeks 3) pada baris terakhir blok (K28)
    total_raw = cell(len(rows) - 1, 3)
    total = _parse_rupiah(total_raw) if total_raw else None

    rincian: list[tuple[str, float]] = []
    for r in range(len(rows) - 1):  # kecuali baris TOTAL
        pemberi = cell(r, 0)
        if not pemberi or pemberi.lower() in ("pemberi", "total"):
            continue
        sisa_raw = cell(r, 3)
        rincian.append((pemberi, _parse_rupiah(sisa_raw) if sisa_raw else 0.0))
    return total, rincian


@_to_async
def get_saldo_dari_dashboard() -> tuple[dict[str, float], float, float]:
    """Kembalikan (saldo_per_akun, total_tanpa_blu, total_dengan_blu)."""
    sheet_name = get_dashboard_sheet_name()
    sheet = _get_sheet(sheet_name)
    rows = sheet.get_values("H4:L25")
    saldo = {}
    for row in rows:
        if not row or not row[0].strip():
            continue
        nama_akun = row[0].strip()
        if "TOTAL" in nama_akun.upper():
            continue
        saldo_ini = _parse_rupiah(row[4]) if len(row) > 4 else 0.0
        saldo[nama_akun] = saldo_ini

    # Baca total langsung dari cell (L15 dan L21)
    total_tanpa_blu = _parse_rupiah(sheet.cell(15, 12).value)
    total_dengan_blu = _parse_rupiah(sheet.cell(21, 12).value)
    return saldo, total_tanpa_blu, total_dengan_blu


def format_saldo_rekap(
    saldo_per_akun: dict[str, float],
    total_tanpa_blu: float | None = None,
    total_dengan_blu: float | None = None,
) -> str:
    if not saldo_per_akun:
        return "Tidak ada data saldo."
    lines = ["💰 *Rekap Saldo per Akun*\n"]
    for akun, saldo in sorted(saldo_per_akun.items()):
        if saldo == 0:
            continue
        lines.append(f"  🏦 {akun}: Rp{saldo:,.2f}")

    if total_tanpa_blu is not None and total_dengan_blu is not None:
        lines.append(f"\n*Total Aset (tanpa Blu Saving): Rp{total_tanpa_blu:,.2f}*")
        lines.append(f"*Total Aset (➕ Blu Saving): Rp{total_dengan_blu:,.2f}*")
    else:
        # Fallback: hitung manual (untuk mode filter akun)
        total = sum(saldo_per_akun.values())
        lines.append(f"\n*Total Aset: Rp{total:,.2f}*")

    return "\n".join(lines)