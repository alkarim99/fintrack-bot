import os
import re
import gspread
from google.oauth2.service_account import Credentials
from config import TRANSACTION_SHEET_NAME, today_str, get_dashboard_sheet_name

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


def get_riwayat(n: int = 10, akun_list: list[str] | None = None) -> list[dict]:
    """Ambil n transaksi terakhir dari Transaction Log. Optional filter by akun."""
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

    start_row = max(2, last_row - n + 1)
    rows = sheet.get_values(f"A{start_row}:G{last_row}")

    result = []
    for row in rows:
        if not row or not row[0].strip():
            continue
        akun = row[3] if len(row) > 3 else ""
        if akun_list and akun not in akun_list:
            continue
        result.append({
            "tanggal": row[0] if len(row) > 0 else "",
            "deskripsi": row[1] if len(row) > 1 else "",
            "kategori": row[2] if len(row) > 2 else "",
            "akun": akun,
            "debit": _parse_rupiah(row[4]) if len(row) > 4 else 0.0,
            "kredit": _parse_rupiah(row[5]) if len(row) > 5 else 0.0,
            "saldo": _parse_rupiah(row[6]) if len(row) > 6 else 0.0,
        })

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


def get_saldo_dari_dashboard() -> dict[str, float]:
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
    return saldo


def format_saldo_rekap(saldo_per_akun: dict[str, float]) -> str:
    if not saldo_per_akun:
        return "Tidak ada data saldo."
    lines = ["💰 *Rekap Saldo per Akun*\n"]
    total = 0.0
    for akun, saldo in sorted(saldo_per_akun.items()):
        if saldo == 0:
            continue
        lines.append(f"  🏦 {akun}: Rp{saldo:,.2f}")
        total += saldo
    lines.append(f"\n*Total Aset: Rp{total:,.2f}*")
    return "\n".join(lines)