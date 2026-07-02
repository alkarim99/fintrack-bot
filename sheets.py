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


def _find_first_empty_row(sheet) -> int:
    col_a = sheet.col_values(1)
    for i, val in enumerate(col_a, start=1):
        if i == 1:
            continue
        if not val.strip():
            return i
    return len(col_a) + 1


def _write_row(sheet, tanggal, deskripsi, kategori, akun, debit, kredit, keterangan=""):
    """
    Tulis satu baris transaksi ke baris kosong pertama.
    Returns: (target_row, new_saldo)
    """
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
    sheet.update(f"A{target_row}:F{target_row}", [row_data], value_input_option="USER_ENTERED")
    sheet.update(f"H{target_row}", [[keterangan]], value_input_option="USER_ENTERED")
    sheet.update(
        f"G{target_row}",
        [[f"=G{prev_row}+E{target_row}-F{target_row}"]],
        value_input_option="USER_ENTERED"
    )
    # Baca saldo hasil formula
    saldo_raw = sheet.cell(target_row, 7).value
    return target_row, _parse_rupiah(saldo_raw or "0")


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


def get_transaksi_hari_ini(tanggal: str) -> list[dict]:
    """Ambil semua transaksi di tanggal tertentu."""
    sheet = _get_sheet(TRANSACTION_SHEET_NAME)
    rows = sheet.get_all_records()
    return [
        r for r in rows
        if str(r.get("Tanggal", "")).strip() == tanggal
        and str(r.get("Akun/Rekening", "")).strip()
    ]


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