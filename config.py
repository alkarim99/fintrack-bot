from datetime import datetime

# ─── Kategori yang valid ───────────────────────────────────────────────────────
VALID_CATEGORIES = [
    "[Kakak] Kos",
    "[Kakak] Bayar makan",
    "[Kakak] Token Listrik",
    "[Kakak] Paket Internet",
    "[Kakak] Bensin",
    "[Kakak] Tiket Bus + Parkir",
    "[Kakak] Subscription AI",
    "[Kakak] Tabungan",
    "[Kakak] Sembako (Sabun & Galon)",
    "[Kakak] Biaya Admin",
    "[Kakak] Income",
    "[Kakak] Sedekah",
    "[Kakak] Pakaian",
    "[Kakak] Bayar parkir",
    "[Pokok] Speedy",
    "[Pokok] Galon qmas",
    "[Pokok] Besmart (Sembako)",
    "[Pokok] Apotek",
    "[Pokok] Kirim ke orang",
    "[Pokok] Bayar makan",
    "[Pokok] Pakaian",
    "[Pokok] Kirim ke orang tua",
    "[Pokok] Bayar iuran",
    "[Pokok] Paket Internet",
    "[Pokok] Bensin",
    "[Adek] Kebutuhan Adek",
    "[Transfer] Internal",
    "[Titipan] Keluar",
    "[Titipan] Masuk",
    "Lain-lain",
]

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
PREFIX_MAP = {
    "kakak": "Kakak",
    "pokok": "Pokok",
    "adek": "Adek",
    "adik": "Adek",
    "titipan": "Titipan",
    "transfer": "Transfer",
    "lain": "Lain",
    "lain-lain": "Lain",
    "lainlain": "Lain",
}

# Prefix yang langsung map ke kategori tanpa fuzzy matching
DIRECT_CATEGORY_MAP = {
    "Lain": "Lain-lain",
}

# ─── Nama sheet di Google Sheets ──────────────────────────────────────────────
TRANSACTION_SHEET_NAME = "Transaction Log"  # sesuaikan dengan nama tab di spreadsheet
TEMP_SHEET_NAME = "Temp"                    # tab sementara sebelum konfirmasi

# ─── Format tanggal ───────────────────────────────────────────────────────────
DATE_FORMAT = "%Y/%m/%d"

MONTH_ABBR = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR",
    5: "MAY", 6: "JUN", 7: "JUL", 8: "AUG",
    9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}

def today_str() -> str:
    return datetime.today().strftime(DATE_FORMAT)

def get_dashboard_sheet_name() -> str:
    """Kembalikan nama sheet dashboard bulan ini, misal: Dash-APR"""
    month = datetime.today().month
    return f"Dash-{MONTH_ABBR[month]}"