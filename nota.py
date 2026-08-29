"""
nota.py — Render gambar nota ABD Food ("AbdFood") gaya kartu invoice.

Layout mengikuti analisis `docs/nota-abd-food.png` (Claude browser / Sonnet 5):
canvas hampir persegi, background tan/peach, font sans-serif, tabel grid solid,
footer blok rekening BCA. Semua konstanta ada di bagian atas agar mudah
diutak-atik. Dirender di resolusi SCALE × lalu di-downscale agar tajam.

Tidak menyentuh Google Sheets — murni generator gambar.
"""

import os
import io

from PIL import Image, ImageDraw, ImageFont


# ─── Canvas & scale ───────────────────────────────────────────────────────────
SCALE = 3
BASE_W = 1080                # lebar akhir (px, 1×); tinggi dinamis per jumlah baris
MIN_BODY_ROWS = 6            # tinggi minimum body tabel (blok kosong bawah tabel)

# ─── Warna ────────────────────────────────────────────────────────────────────
COLOR_BG = (232, 201, 160)   # tan/peach
COLOR_TEXT = (35, 25, 15)    # coklat tua
COLOR_LINE = (60, 45, 30)    # border & underline

# ─── Teks statis (bisa disesuaikan) ───────────────────────────────────────────
NAMA_USAHA = "AbdFood"
ALAMAT = ["Pondok Blimbing Indah K3-5", "082139620729"]

FOOTER_INFO = {
    "bank": "BCA",
    "rekening": "3310766207",
    "atas_nama": "A.n. Abdullah Al-Karim A",
}

# ─── Label ────────────────────────────────────────────────────────────────────
LABEL_TANGGAL = "Tanggal"
LABEL_KEPADA = "Kepada"
COL_HEADERS = ["Banyak", "Nama Barang", "Harga", "Jumlah"]
COL_ALIGN = ["center", "left", "right", "right"]
TOTAL_LABEL = "JUMLAH"
ONGKIR_DEFAULT_LABEL = "Ongkir"

# ─── Font & ukuran (px, di skala 1×) ──────────────────────────────────────────
FONT_SIZE_TITLE = 80      # "AbdFood"
FONT_SIZE_ADDRESS = 26    # alamat + no. HP
FONT_SIZE_LABEL = 32      # label "Tanggal", "Kepada"
FONT_SIZE_VALUE = 32      # isi tanggal/kepada + isi tabel
FONT_SIZE_HEADER = 30     # header kolom tabel
FONT_SIZE_TOTAL = 34      # label "JUMLAH" + angka total
FONT_SIZE_FOOTER = 26     # blok BCA

# ─── Header kiri ──────────────────────────────────────────────────────────────
MARGIN_LEFT = 60
HEADER_TITLE_Y = 90
ALAMAT_Y0 = 195
ALAMAT_Y1 = 225

# ─── Field kanan: Tanggal & Kepada (solid underline di bawah value) ───────────
FIELD_LABEL_X = 570
FIELD_VALUE_LINE_END_X = 1013
FIELD_TANGGAL_Y = 135
FIELD_KEPADA_Y = 210
FIELD_UNDERLINE_OFFSET_Y = 12
FIELD_LABEL_VALUE_GAP = 24   # px minimum antara akhir teks label dan awal value

# ─── Tabel ────────────────────────────────────────────────────────────────────
TABLE_Y0 = 335
HEADER_ROW_H = 90
HEADER_PAD_TOP = 6         # padding atas teks header kolom (~12px visual setelah ascender font)
ROW_H = 42
TOTAL_ROW_H = 95

COL_BANYAK_X0 = 60
COL_BANYAK_W = 185            # cukup lebar agar header "Banyak" muat penuh
COL_BARANG_X0 = COL_BANYAK_X0 + COL_BANYAK_W
COL_BARANG_W = 400
COL_HARGA_X0 = COL_BARANG_X0 + COL_BARANG_W
COL_HARGA_W = 180
COL_JUMLAH_X0 = COL_HARGA_X0 + COL_HARGA_W
COL_JUMLAH_W = 195
TABLE_X1 = COL_JUMLAH_X0 + COL_JUMLAH_W   # 1020
COL_PAD_X = 16

# ─── Footer blok BCA ──────────────────────────────────────────────────────────
FOOTER_GAP = 60           # jarak dari bawah tabel
FOOTER_LOGO_W = 110
FOOTER_LOGO_H = 56
FOOTER_TEXT_GAP = 20
FOOTER_LINE_GAP = 30
MARGIN_BOTTOM = 30

# ─── Garis ────────────────────────────────────────────────────────────────────
LINE_WIDTH = 2            # solid, bukan dashed


# ─── Font ─────────────────────────────────────────────────────────────────────
_FONT_FILES = {
    "regular": "DejaVuSans.ttf",
    "bold": "DejaVuSans-Bold.ttf",
}
_FONT_FALLBACK = {
    "regular": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ],
    "bold": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ],
}
_font_cache: dict[tuple, ImageFont.FreeTypeFont] = {}


def _load_font(size: int, bold: bool = False):
    """Muat font sans-serif (regular/bold); fallback ke font sistem lalu bawaan Pillow."""
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    style = "bold" if bold else "regular"
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", _FONT_FILES[style]),
    ] + _FONT_FALLBACK[style]
    for path in candidates:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size)
                _font_cache[key] = font
                return font
            except Exception:
                continue
    font = ImageFont.load_default()
    _font_cache[key] = font
    return font


# ─── Helper teks ──────────────────────────────────────────────────────────────
_BULAN = {
    1: "Januari", 2: "Februari", 3: "Maret", 4: "April", 5: "Mei", 6: "Juni",
    7: "Juli", 8: "Agustus", 9: "September", 10: "Oktober", 11: "November", 12: "Desember",
}


def tanggal_indonesia(iso: str) -> str:
    """Ubah 'YYYY/MM/DD' → teks Indonesia, mis. '20 Agustus 2026'."""
    try:
        y, m, d = iso.split("/")
        return f"{int(d)} {_BULAN[int(m)]} {int(y)}"
    except (ValueError, KeyError):
        return iso


def _angka(n) -> str:
    """Angka polos gaya Indonesia: titik ribuan, tanpa desimal. Contoh: 65000 → '65.000'."""
    return f"{round(float(n)):,}".replace(",", ".")


def _format_qty(qty) -> str:
    """Qty bulat tampil tanpa desimal ('10' bukan '10.0'), pecahan tetap ('2.5')."""
    qty = float(qty)
    if qty == int(qty):
        return str(int(qty))
    return f"{qty:g}"


def _fit_width(draw, text: str, font, max_w: int) -> str:
    """Potong teks (tambah '..') bila lebih lebar dari max_w pixel."""
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "..", font=font) > max_w:
        text = text[:-1]
    return text + ".."


_bca_logo_cache: Image.Image | None = None


def _bca_logo() -> Image.Image:
    """Logo BCA (assets/bca.png, RGBA transparan) — dimuat sekali lalu di-cache."""
    global _bca_logo_cache
    if _bca_logo_cache is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "bca.png")
        _bca_logo_cache = Image.open(path).convert("RGBA")
    return _bca_logo_cache


# ─── Renderer ─────────────────────────────────────────────────────────────────

def render_nota_image(tanggal: str, nama: str, items: list[tuple], total: float,
                      ongkir: tuple | None = None) -> bytes:
    """Render nota menjadi PNG bytes.

    Args:
        tanggal: tanggal 'YYYY/MM/DD' (ditampilkan sebagai teks Indonesia).
        nama:    nama penerima nota (label 'Kepada').
        items:   list of (qty, nama_barang, harga_satuan); qty/harga bisa str
                 atau angka; subtotal dihitung di sini (qty × harga).
        total:   total belanja = Σ subtotal + ongkir.
        ongkir:  None atau (label, nominal) — baris tabel tanpa qty/harga,
                 langsung mengisi kolom Jumlah.

    Returns:
        bytes PNG siap dikirim via Telegram (reply_photo).
    """
    S = SCALE
    n_rows = len(items) + (1 if ongkir else 0)
    body_rows = max(n_rows, MIN_BODY_ROWS)

    table_h = HEADER_ROW_H + body_rows * ROW_H + TOTAL_ROW_H
    table_bottom = TABLE_Y0 + table_h
    footer_h = max(FOOTER_LOGO_H, FOOTER_LINE_GAP * 3 + 8)  # BCA + rekening + a.n.
    H = table_bottom + FOOTER_GAP + footer_h + MARGIN_BOTTOM
    W = BASE_W

    img = Image.new("RGB", (W * S, H * S), COLOR_BG)
    draw = ImageDraw.Draw(img)

    ft_title = _load_font(FONT_SIZE_TITLE * S, bold=True)
    ft_addr = _load_font(FONT_SIZE_ADDRESS * S)
    ft_field_label = _load_font(FONT_SIZE_LABEL * S)
    ft_field_value = _load_font(FONT_SIZE_VALUE * S)
    ft_head = _load_font(FONT_SIZE_HEADER * S, bold=True)
    ft_row = _load_font(FONT_SIZE_VALUE * S)
    ft_total = _load_font(FONT_SIZE_TOTAL * S, bold=True)
    ft_footer = _load_font(FONT_SIZE_FOOTER * S)
    ft_bank = _load_font(FONT_SIZE_FOOTER * S, bold=True)

    def tx(x, y, text, font, fill=COLOR_TEXT, anchor="la"):
        draw.text((x * S, y * S), text, font=font, fill=fill, anchor=anchor)

    def cell(x0, x1, y, text, font, align, fill=COLOR_TEXT, top=False):
        """Gambar teks dalam satu kolom tabel (rata kiri/kanan/tengah), auto-truncate.

        top=True → y diperlakukan sebagai posisi atas teks (bukan tengah baris),
        dipakai untuk header kolom dengan padding atas.
        """
        max_w = (x1 - x0) * S - 2 * COL_PAD_X * S
        text = _fit_width(draw, text, font, max_w)
        yp = y * S
        if align == "right":
            anchor = "ra" if top else "rm"
            draw.text((x1 * S - COL_PAD_X * S, yp), text, font=font, fill=fill, anchor=anchor)
        elif align == "center":
            anchor = "ma" if top else "mm"
            draw.text(((x0 + x1) * S / 2, yp), text, font=font, fill=fill, anchor=anchor)
        else:  # left
            anchor = "la" if top else "lm"
            draw.text((x0 * S + COL_PAD_X * S, yp), text, font=font, fill=fill, anchor=anchor)

    # ── Header kiri: nama usaha + alamat ───────────────────────────────────
    tx(MARGIN_LEFT, HEADER_TITLE_Y, NAMA_USAHA, ft_title)
    tx(MARGIN_LEFT, ALAMAT_Y0, ALAMAT[0], ft_addr)
    tx(MARGIN_LEFT, ALAMAT_Y1, ALAMAT[1], ft_addr)

    # ── Field kanan: Tanggal & Kepada (label + value + underline) ──────────
    tgl_text = tanggal_indonesia(tanggal)
    for label, value, y_field in [
        (LABEL_TANGGAL, tgl_text, FIELD_TANGGAL_Y),
        (LABEL_KEPADA, nama, FIELD_KEPADA_Y),
    ]:
        tx(FIELD_LABEL_X, y_field, label, ft_field_label)
        label_w = draw.textlength(label, font=ft_field_label)
        value_x = FIELD_LABEL_X + label_w / S + FIELD_LABEL_VALUE_GAP
        max_w = (FIELD_VALUE_LINE_END_X - value_x) * S
        value_text = _fit_width(draw, value, ft_field_value, max_w)
        tx(value_x, y_field, value_text, ft_field_value)
        uy = (y_field + FONT_SIZE_VALUE + FIELD_UNDERLINE_OFFSET_Y) * S
        draw.line([(value_x * S, uy), (FIELD_VALUE_LINE_END_X * S, uy)],
                  fill=COLOR_LINE, width=LINE_WIDTH * S)

    # ── Tabel ──────────────────────────────────────────────────────────────
    x0 = MARGIN_LEFT * S
    x1 = TABLE_X1 * S
    y0 = TABLE_Y0 * S
    y_bottom = y0 + (HEADER_ROW_H + body_rows * ROW_H + TOTAL_ROW_H) * S

    # Border luar
    draw.rectangle([x0, y0, x1, y_bottom], outline=COLOR_LINE, width=LINE_WIDTH * S)
    # Pemisah kolom vertikal
    for cx in (COL_BANYAK_X0, COL_BARANG_X0, COL_HARGA_X0, COL_JUMLAH_X0):
        draw.line([(cx * S, y0), (cx * S, y_bottom)], fill=COLOR_LINE, width=LINE_WIDTH * S)
    # Garis horizontal bawah header
    draw.line([(x0, y0 + HEADER_ROW_H * S), (x1, y0 + HEADER_ROW_H * S)],
              fill=COLOR_LINE, width=LINE_WIDTH * S)
    # Garis horizontal HANYA antar baris konten (item + ongkir), bukan di blok kosong
    for i in range(1, n_rows):
        yy = y0 + (HEADER_ROW_H + i * ROW_H) * S
        draw.line([(x0, yy), (x1, yy)], fill=COLOR_LINE, width=LINE_WIDTH * S)
    # Garis pemisah blok kosong → baris TOTAL
    yy = y0 + (HEADER_ROW_H + body_rows * ROW_H) * S
    draw.line([(x0, yy), (x1, yy)], fill=COLOR_LINE, width=LINE_WIDTH * S)

    # Header kolom (padding atas, agar tidak nempel ke garis atas tabel)
    col_x = [(COL_BANYAK_X0, COL_BARANG_X0), (COL_BARANG_X0, COL_HARGA_X0),
             (COL_HARGA_X0, COL_JUMLAH_X0), (COL_JUMLAH_X0, TABLE_X1)]
    hy = TABLE_Y0 + HEADER_PAD_TOP
    for i, header in enumerate(COL_HEADERS):
        cell(col_x[i][0], col_x[i][1], hy, header, ft_head, COL_ALIGN[i], top=True)

    # Baris item
    for i, (qty, nama_barang, harga) in enumerate(items):
        yc = TABLE_Y0 + HEADER_ROW_H + (i + 0.5) * ROW_H
        cell(COL_BANYAK_X0, COL_BARANG_X0, yc, _format_qty(qty), ft_row, "center")
        cell(COL_BARANG_X0, COL_HARGA_X0, yc, nama_barang, ft_row, "left")
        cell(COL_HARGA_X0, COL_JUMLAH_X0, yc, _angka(harga), ft_row, "right")
        cell(COL_JUMLAH_X0, TABLE_X1, yc, _angka(float(qty) * float(harga)), ft_row, "right")

    # Baris ongkir (tanpa qty/harga, langsung isi kolom Jumlah)
    if ongkir:
        label, nominal = ongkir
        i = len(items)
        yc = TABLE_Y0 + HEADER_ROW_H + (i + 0.5) * ROW_H
        cell(COL_BANYAK_X0, COL_BARANG_X0, yc, "", ft_row, "center")
        cell(COL_BARANG_X0, COL_HARGA_X0, yc, label, ft_row, "left")
        cell(COL_HARGA_X0, COL_JUMLAH_X0, yc, "", ft_row, "right")
        cell(COL_JUMLAH_X0, TABLE_X1, yc, _angka(nominal), ft_row, "right")

    # Baris total: label span 3 kolom kiri (rata kanan), angka di kolom Jumlah
    ty = TABLE_Y0 + HEADER_ROW_H + body_rows * ROW_H + TOTAL_ROW_H / 2
    cell(COL_BANYAK_X0, COL_JUMLAH_X0, ty, TOTAL_LABEL, ft_total, "right")
    cell(COL_JUMLAH_X0, TABLE_X1, ty, _angka(total), ft_total, "right")

    # ── Footer blok BCA ────────────────────────────────────────────────────
    fy = table_bottom + FOOTER_GAP
    lx0 = MARGIN_LEFT * S
    ly0 = fy * S
    lx1 = (MARGIN_LEFT + FOOTER_LOGO_W) * S
    ly1 = (fy + FOOTER_LOGO_H) * S

    # Logo BCA asli (transparan), disesuaikan proporsinya di dalam kotak logo.
    logo = _bca_logo()
    logo_w, logo_h = logo.size
    box_w, box_h = lx1 - lx0, ly1 - ly0
    fit = min(box_w / logo_w, box_h / logo_h)
    lw, lh = int(logo_w * fit), int(logo_h * fit)
    logo_resized = logo.resize((lw, lh), Image.LANCZOS)
    img.paste(logo_resized, (int(lx0 + (box_w - lw) / 2), int(ly0 + (box_h - lh) / 2)), logo_resized)

    # Teks di kanan logo: BCA (bold), no. rekening, a.n.
    fx = (MARGIN_LEFT + FOOTER_LOGO_W + FOOTER_TEXT_GAP) * S
    ty0 = (fy + 2) * S
    draw.text((fx, ty0), FOOTER_INFO["bank"], font=ft_bank, fill=COLOR_TEXT, anchor="la")
    draw.text((fx, (fy + 2 + FOOTER_LINE_GAP) * S), FOOTER_INFO["rekening"],
              font=ft_footer, fill=COLOR_TEXT, anchor="la")
    draw.text((fx, (fy + 2 + 2 * FOOTER_LINE_GAP) * S), FOOTER_INFO["atas_nama"],
              font=ft_footer, fill=COLOR_TEXT, anchor="la")

    # ── Downscale → tepian halus ───────────────────────────────────────────
    img = img.resize((W, H), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
