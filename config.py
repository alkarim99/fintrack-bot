from datetime import datetime
from zoneinfo import ZoneInfo

# ─── Timezone ───────────────────────────────────────────────────────────────────
WIB = ZoneInfo("Asia/Jakarta")

# ─── Kategori valid + Tipe ──────────────────────────────────────────────────────
# Tipe menentukan cara laporan memperlakukan kategori:
#   "pemasukan"   → dihitung sebagai income
#   "pengeluaran" → dihitung sebagai expense
#   "tabungan"    → netral (pindah ke tabungan, bukan expense)
#   "passthrough" → pos NERACA (transfer/kas RT/titipan/hutang/piutang), JANGAN masuk untung-rugi
#
# Sumber kebenaran utama: sheet "Data Master" (dibaca saat startup, lihat sheets.get_master_categories).
# Dict ini = fallback bila sheet tak terbaca.
CATEGORY_TYPES: dict[str, str] = {
    # ── Pemasukan ──
    "[Income] Pribadi": "pemasukan",
    "[Bisnis] Income": "pemasukan",
    # ── Bisnis (modal = pengeluaran) ──
    "[Bisnis] Modal": "pengeluaran",
    # ── Pokok (bapak/mama) ──
    "[Pokok] Kirim ke orang tua": "pengeluaran",
    "[Pokok] Kirim ke orang": "pengeluaran",
    "[Pokok] Bayar makan": "pengeluaran",
    "[Pokok] Besmart (Sembako)": "pengeluaran",
    "[Pokok] Galon qmas": "pengeluaran",
    "[Pokok] Apotek": "pengeluaran",
    "[Pokok] Kesehatan": "pengeluaran",
    "[Pokok] Bensin": "pengeluaran",
    "[Pokok] Kendaraan": "pengeluaran",
    "[Pokok] Speedy": "pengeluaran",
    "[Pokok] Paket Internet": "pengeluaran",
    "[Pokok] Pakaian": "pengeluaran",
    "[Pokok] Peralatan Rumah": "pengeluaran",
    "[Pokok] Peralatan Kerja": "pengeluaran",
    "[Pokok] Bayar iuran": "pengeluaran",
    "[Pokok] Zakat": "pengeluaran",
    "[Pokok] Sedekah": "pengeluaran",
    # ── Kakak (pribadi) ──
    "[Kakak] Kos": "pengeluaran",
    "[Kakak] Bayar makan": "pengeluaran",
    "[Kakak] Transport": "pengeluaran",
    "[Kakak] Bensin": "pengeluaran",
    "[Kakak] Kendaraan": "pengeluaran",
    "[Kakak] Sembako (Sabun & Galon)": "pengeluaran",
    "[Kakak] Token Listrik": "pengeluaran",
    "[Kakak] Paket Internet": "pengeluaran",
    "[Kakak] Subscription AI": "pengeluaran",
    "[Kakak] Sedekah": "pengeluaran",
    "[Kakak] Perawatan Diri": "pengeluaran",
    "[Kakak] Kesehatan": "pengeluaran",
    "[Kakak] Pendidikan / Buku": "pengeluaran",
    "[Kakak] Hiburan / Wisata": "pengeluaran",
    "[Kakak] Pakaian": "pengeluaran",
    "[Kakak] Peralatan Kerja": "pengeluaran",
    "[Kakak] Biaya Admin": "pengeluaran",
    "[Kakak] Pajak": "pengeluaran",
    "[Kakak] Tabungan": "tabungan",
    # ── Adek & lain ──
    "[Adek] Kebutuhan Adek": "pengeluaran",
    "Lain-lain": "pengeluaran",
    # ── NERACA / pass-through (JANGAN dihitung untung-rugi) ──
    "[Transfer] Internal": "passthrough",
    "[Kas RT] Masuk": "passthrough",
    "[Kas RT] Keluar": "passthrough",
    "[Titipan] Masuk": "passthrough",
    "[Titipan] Keluar": "passthrough",
    "[Hutang] Terima": "passthrough",
    "[Hutang] Bayar": "passthrough",
    "[Piutang] Tambah": "passthrough",
    "[Piutang] Terima": "passthrough",
}

VALID_CATEGORIES = list(CATEGORY_TYPES.keys())

# Prefix pos neraca — dipakai sebagai fallback penentuan tipe bila kategori tak terdaftar
PASSTHROUGH_PREFIXES = ("[Transfer]", "[Kas RT]", "[Titipan]", "[Hutang]", "[Piutang]")


def category_type(kategori: str) -> str:
    """Kembalikan tipe kategori. Fallback berdasarkan prefix bila tak terdaftar."""
    if kategori in CATEGORY_TYPES:
        return CATEGORY_TYPES[kategori]
    for p in PASSTHROUGH_PREFIXES:
        if kategori.startswith(p):
            return "passthrough"
    return "pengeluaran"  # default aman


def is_passthrough(kategori: str) -> bool:
    """True bila kategori adalah pos neraca (tidak dihitung di untung-rugi)."""
    return category_type(kategori) == "passthrough"

# ─── Rekening yang valid ───────────────────────────────────────────────────────
VALID_ACCOUNTS = [
    "Mandiri",
    "BCA",
    "Blu Account",
    "Blu Saving",
    "Jago Main",
    "Jago Monthly",
    "Jago Saving",
    "Jago Deposit",
    "Cash",
    "Bibit - RDPU",
    "Bibit - RDO",
    "Bibit - RDS",
]

# ─── Mapping prefix input → prefix kategori ───────────────────────────────────
# Mendukung prefix multi-kata (mis. "kas rt"). Lihat matcher.resolve_prefix.
PREFIX_MAP = {
    "kakak": "Kakak",
    "pokok": "Pokok",
    "adek": "Adek",
    "adik": "Adek",
    "income": "Income",
    "gaji": "Income",
    "bisnis": "Bisnis",
    "usaha": "Bisnis",
    "titipan": "Titipan",
    "kas rt": "Kas RT",
    "kasrt": "Kas RT",
    "rt": "Kas RT",
    "hutang": "Hutang",
    "utang": "Hutang",
    "piutang": "Piutang",
    "transfer": "Transfer",
    "lain": "Lain",
    "lain-lain": "Lain",
    "lainlain": "Lain",
}

# Prefix yang subkategorinya PASTI (satu opsi) — tak perlu fuzzy matching.
SINGLE_CATEGORY_MAP = {
    "Income": "[Income] Pribadi",
    "Adek": "[Adek] Kebutuhan Adek",
    "Transfer": "[Transfer] Internal",
    "Lain": "Lain-lain",
}

# Prefix yang subkategorinya ditentukan oleh jenis (masuk/keluar).
DIRECTIONAL_CATEGORY_MAP = {
    "Kas RT":  {"masuk": "[Kas RT] Masuk",   "keluar": "[Kas RT] Keluar"},
    "Titipan": {"masuk": "[Titipan] Masuk",  "keluar": "[Titipan] Keluar"},
    "Hutang":  {"masuk": "[Hutang] Terima",  "keluar": "[Hutang] Bayar"},
    "Piutang": {"masuk": "[Piutang] Terima", "keluar": "[Piutang] Tambah"},
    "Bisnis":  {"masuk": "[Bisnis] Income",  "keluar": "[Bisnis] Modal"},
}


def resolve_forced_category(prefix_std: str, jenis: str) -> str | None:
    """Kembalikan kategori pasti untuk prefix single/directional, atau None bila perlu fuzzy match."""
    if prefix_std in SINGLE_CATEGORY_MAP:
        return SINGLE_CATEGORY_MAP[prefix_std]
    if prefix_std in DIRECTIONAL_CATEGORY_MAP:
        return DIRECTIONAL_CATEGORY_MAP[prefix_std].get(jenis)
    return None

# ─── Nama sheet di Google Sheets ──────────────────────────────────────────────
TRANSACTION_SHEET_NAME = "Transaction Log"  # sesuaikan dengan nama tab di spreadsheet
TEMP_SHEET_NAME = "Temp"                    # tab sementara sebelum konfirmasi
MASTER_SHEET_NAME = "Data Master"           # tab berisi daftar kategori (kolom: Kategori, Tipe)

# ─── Format tanggal ───────────────────────────────────────────────────────────
DATE_FORMAT = "%Y/%m/%d"

MONTH_ABBR = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR",
    5: "MAY", 6: "JUN", 7: "JUL", 8: "AUG",
    9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}

def today_str() -> str:
    return datetime.now(WIB).strftime(DATE_FORMAT)

def get_dashboard_sheet_name() -> str:
    """Kembalikan nama sheet dashboard bulan ini, misal: Dash-APR"""
    month = datetime.now(WIB).month
    return f"Dash-{MONTH_ABBR[month]}"