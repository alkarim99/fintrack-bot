"""
Fintrack Bot — Telegram bot untuk pencatatan keuangan ke Google Sheets.
"""

import os
import re
import logging
from dotenv import load_dotenv

load_dotenv()

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import config
from config import today_str, resolve_forced_category, category_type, get_dashboard_sheet_name, WIB
from matcher import match_category, best_match, match_account, format_category_choices, resolve_prefix
from sheets import (
    append_transaction,
    append_transfer,
    get_saldo_dari_dashboard,
    get_riwayat,
    get_transaksi_hari_ini,
    get_all_transactions,
    get_kas_rt_buku,
    get_hutang_from_dashboard,
    format_saldo_rekap,
    get_master_categories,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# { chat_id: { "stage": str, "data": dict } }
user_state: dict[int, dict] = {}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def parse_nominal(raw: str) -> float | None:
    """
    Parse nominal dengan dukungan desimal dan thousand separator.

    Contoh:
      4,5jt   → 4.5 × 1,000,000 = 4,500,000
      4.5jt   → 4.5 × 1,000,000 = 4,500,000
      100,500.90 → 100500.90
      1.500.000  → 1500000 (titik = thousand separator)
      15rb    → 15 × 1,000 = 15,000
      500000  → 500,000
    """
    raw = raw.strip().lower()

    # 1. Extract suffix multiplier
    multiplier = 1
    for suffix, mult in [("rb", 1_000), ("k", 1_000), ("jt", 1_000_000), ("m", 1_000_000)]:
        if raw.endswith(suffix):
            multiplier = mult
            raw = raw[:-len(suffix)].strip()
            break

    # 2. Detect decimal vs thousand separators
    has_comma = "," in raw
    has_period = "." in raw

    if has_comma and has_period:
        # Both present: last one is decimal separator
        last_comma = raw.rfind(",")
        last_period = raw.rfind(".")
        if last_comma > last_period:
            # Comma is decimal (e.g. "1.500,90")
            raw = raw.replace(".", "")   # remove thousand sep
            raw = raw.replace(",", ".")  # decimal comma → period
        else:
            # Period is decimal (e.g. "1,500.90")
            raw = raw.replace(",", "")   # remove thousand sep
    elif has_comma:
        # Only comma: check if decimal (1-2 digits after last comma)
        last_comma = raw.rfind(",")
        after = raw[last_comma + 1:]
        if len(after) <= 2 and after.isdigit():
            raw = raw.replace(",", ".")  # decimal comma → period
        else:
            raw = raw.replace(",", "")   # thousand separator
    elif has_period:
        # Only period: check if decimal (1-2 digits after last period)
        last_period = raw.rfind(".")
        after = raw[last_period + 1:]
        if len(after) <= 2 and after.isdigit():
            pass  # already decimal, keep as-is
        else:
            raw = raw.replace(".", "")   # thousand separator

    try:
        return float(raw) * multiplier
    except ValueError:
        return None


def parse_tanggal(raw: str) -> str | None:
    from datetime import datetime
    raw = raw.strip()
    for fmt in ["%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%Y-%m-%d"]:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y/%m/%d")
        except ValueError:
            continue
    return None


def parse_transaction_message(text: str) -> dict | str:
    required_keys = {"jenis", "prefix", "deskripsi", "akun", "nominal"}
    optional_keys = {"tanggal"}
    result = {}

    for line in text.strip().splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if key in required_keys | optional_keys:
            result[key] = value

    missing = required_keys - result.keys()
    if missing:
        return f"Field yang kurang: {', '.join(sorted(missing))}"

    jenis = result["jenis"].lower()
    if jenis not in ("masuk", "keluar"):
        return "Field 'jenis' harus diisi 'masuk' atau 'keluar'."
    result["jenis"] = jenis

    nominal = parse_nominal(result["nominal"])
    if nominal is None or nominal <= 0:
        return f"Nominal '{result['nominal']}' tidak bisa dibaca. Contoh: 35rb, 1.5jt, 500000."
    result["nominal_float"] = nominal

    if "tanggal" in result and result["tanggal"]:
        parsed_tgl = parse_tanggal(result["tanggal"])
        if parsed_tgl is None:
            return f"Format tanggal '{result['tanggal']}' tidak dikenali. Gunakan: DD/MM/YYYY"
        result["tanggal"] = parsed_tgl
    else:
        result["tanggal"] = today_str()

    return result


def parse_transfer_message(text: str) -> dict | str:
    required_keys = {"jenis", "dari", "ke", "nominal"}
    optional_keys = {"tanggal", "kode_unik"}
    result = {}

    for line in text.strip().splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if key in required_keys | optional_keys:
            result[key] = value

    missing = required_keys - result.keys()
    if missing:
        return f"Field yang kurang: {', '.join(sorted(missing))}"

    if result.get("jenis", "").lower() != "transfer":
        return None  # bukan pesan transfer, lanjut ke parse biasa

    nominal = parse_nominal(result["nominal"])
    if nominal is None or nominal <= 0:
        return f"Nominal '{result['nominal']}' tidak bisa dibaca."
    result["nominal_float"] = nominal

    # Parse kode_unik (opsional, default 0)
    kode_unik = 0.0
    if "kode_unik" in result and result["kode_unik"]:
        ku = parse_nominal(result["kode_unik"])
        if ku is None or ku < 0:
            return f"Kode unik '{result['kode_unik']}' tidak bisa dibaca."
        kode_unik = ku
    result["kode_unik_float"] = kode_unik

    if "tanggal" in result and result["tanggal"]:
        parsed_tgl = parse_tanggal(result["tanggal"])
        if parsed_tgl is None:
            return f"Format tanggal '{result['tanggal']}' tidak dikenali. Gunakan: DD/MM/YYYY"
        result["tanggal"] = parsed_tgl
    else:
        result["tanggal"] = today_str()

    return result


def build_preview(data: dict) -> str:
    jenis_label = "⬆️ Masuk" if data["jenis"] == "masuk" else "⬇️ Keluar"
    return (
        f"📋 *Preview Transaksi*\n\n"
        f"📅 Tanggal   : {data['tanggal']}\n"
        f"📝 Deskripsi : {data['deskripsi']}\n"
        f"🏷️ Kategori  : {data['kategori']}\n"
        f"🏦 Akun      : {data['akun']}\n"
        f"{jenis_label} : Rp{data['nominal_float']:,.2f}\n\n"
        f"Balas *ok* untuk simpan, atau *batal* untuk membatalkan."
    )


def build_transfer_preview(data: dict) -> str:
    if data["ke"].lower() == "cash":
        deskripsi = f"Tarik tunai dari {data['dari']}"
    else:
        deskripsi = f"{data['dari']} → {data['ke']}"

    kode_unik = data.get("kode_unik_float", 0.0)
    total_keluar = data["nominal_float"] + kode_unik

    lines = [
        f"📋 *Preview Transfer Internal*\n",
        f"📅 Tanggal : {data['tanggal']}\n",
        f"↔️ Transfer : {deskripsi}\n",
        f"💸 Nominal  : Rp{data['nominal_float']:,.2f}\n",
    ]
    if kode_unik > 0:
        lines.append(f"🔢 Kode Unik: Rp{kode_unik:,.2f}\n")
        lines.append(f"📤 Total Keluar: Rp{total_keluar:,.2f}\n")
    lines.append(f"\nAkan dicatat sebagai *2 baris* di Transaction Log.\n")
    lines.append(f"\nBalas *ok* untuk simpan, atau *batal* untuk membatalkan.")
    return "".join(lines)


# ─── Command Handlers ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Halo! Saya *Fintrack Bot*.\n\n"
        "Gunakan /help untuk melihat semua perintah yang tersedia.",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Daftar Perintah Fintrack Bot*\n\n"
        "💳 *Transaksi*\n"
        "  /format — Template input transaksi\n"
        "  /transfer — Template input transfer internal (mendukung kode unik Flip)\n\n"
        "📊 *Laporan*\n"
        "  /saldo — Rekap saldo per akun. Filter: /saldo BCA, Mandiri\n"
        "  /riwayat — 10 transaksi terakhir. Filter: /riwayat BCA\n"
        "  /hari\\_ini — Ringkasan transaksi hari ini\n"
        "  /anggaran — Realisasi belanja vs target bulan ini\n\n"
        "🧮 *Neraca*\n"
        "  /hutang — Sisa hutang berjalan\n"
        "  /piutang — Sisa piutang berjalan\n"
        "  /kasrt — Rekonsiliasi kas RT. Cek lubang: /kasrt 19584000\n\n"
        "⚙️ *Lainnya*\n"
        "  /batal — Batalkan input yang sedang berjalan\n"
        "  /help — Tampilkan pesan ini\n\n"
        "💡 Atau langsung kirim transaksi tanpa command menggunakan format dari /format",
        parse_mode="Markdown",
    )


async def cmd_saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Mengambil data saldo...")
    try:
        saldo, total_tanpa_blu, total_dengan_blu = get_saldo_dari_dashboard()

        # Filter by account jika ada argumen (contoh: /saldo BCA, Mandiri)
        if context.args:
            akun_inputs = " ".join(context.args).split(",")
            akun_list = []
            not_found = []
            for a in akun_inputs:
                a = a.strip()
                if not a:
                    continue
                matched = match_account(a)
                if matched:
                    akun_list.append(matched)
                else:
                    not_found.append(a)

            if not_found:
                await update.message.reply_text(
                    f"⚠️ Akun tidak dikenali: {', '.join(not_found)}"
                )
                if not akun_list:
                    return

            saldo = {k: v for k, v in saldo.items() if k in akun_list}
            # Filter mode: hitung manual, tidak pakai total dashboard
            rekap = format_saldo_rekap(saldo)
        else:
            rekap = format_saldo_rekap(saldo, total_tanpa_blu, total_dengan_blu)
        await update.message.reply_text(rekap, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error get saldo: {e}")
        await update.message.reply_text(f"❌ Gagal ambil saldo: {e}")


async def cmd_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 *Template Transaksi*\n\n"
        "Salin, isi, lalu kirim:\n"
        "```\n"
        "jenis = \n"
        "prefix = \n"
        "deskripsi = \n"
        "akun = \n"
        "nominal = \n"
        "```\n\n"
        "💡 *Contoh:*\n"
        "```\n"
        "jenis = keluar\n"
        "prefix = kakak\n"
        "deskripsi = bayar makan soto lamongan\n"
        "akun = BCA\n"
        "nominal = 15rb\n"
        "```\n\n"
        "📌 *Jenis:* masuk / keluar\n"
        "📌 *Prefix:* kakak / pokok / adek / income / bisnis\n"
        "     _pos neraca:_ kas rt / titipan / hutang / piutang\n"
        "     (subkategori pos neraca otomatis dari jenis: masuk/keluar)\n"
        "📌 *Nominal:* 15rb, 1.5jt, 500000\n"
        "📌 *Tanggal (opsional):* tambahkan `tanggal = 25/04/2026` jika bukan hari ini\n\n"
        "💡 *Contoh terima pinjaman:*\n"
        "```\n"
        "jenis = masuk\n"
        "prefix = hutang\n"
        "deskripsi = pinjaman dari bude hajar\n"
        "akun = Mandiri\n"
        "nominal = 5jt\n"
        "```",
        parse_mode="Markdown",
    )


async def cmd_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 *Template Transfer Internal*\n\n"
        "Salin, isi, lalu kirim:\n"
        "```\n"
        "jenis = transfer\n"
        "dari = \n"
        "ke = \n"
        "nominal = \n"
        "kode_unik = \n"
        "```\n\n"
        "💡 *Contoh transfer biasa:*\n"
        "```\n"
        "jenis = transfer\n"
        "dari = BCA\n"
        "ke = Jago Main\n"
        "nominal = 500rb\n"
        "```\n\n"
        "💡 *Contoh transfer Flip (dengan kode unik):*\n"
        "```\n"
        "jenis = transfer\n"
        "dari = Mandiri\n"
        "ke = BCA\n"
        "nominal = 500000\n"
        "kode_unik = 325\n"
        "```\n\n"
        "💡 *Contoh tarik tunai:*\n"
        "```\n"
        "jenis = transfer\n"
        "dari = BCA\n"
        "ke = Cash\n"
        "nominal = 200rb\n"
        "```\n\n"
        "📌 *kode\\_unik:* opsional, untuk transfer Flip (selisih biaya admin)\n"
        "📌 *Tanggal (opsional):* tambahkan `tanggal = 25/04/2026` jika bukan hari ini",
        parse_mode="Markdown",
    )


async def cmd_riwayat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Mengambil riwayat transaksi...")
    try:
        # Filter by account jika ada argumen (contoh: /riwayat BCA)
        akun_list = None
        if context.args:
            akun_inputs = " ".join(context.args).split(",")
            akun_list = []
            not_found = []
            for a in akun_inputs:
                a = a.strip()
                if not a:
                    continue
                matched = match_account(a)
                if matched:
                    akun_list.append(matched)
                else:
                    not_found.append(a)

            if not_found:
                await update.message.reply_text(
                    f"⚠️ Akun tidak dikenali: {', '.join(not_found)}"
                )
                if not akun_list:
                    return

        rows = get_riwayat(n=10, akun_list=akun_list)
        if not rows:
            filter_info = f" untuk akun {', '.join(akun_list)}" if akun_list else ""
            await update.message.reply_text(f"Belum ada transaksi{filter_info}.")
            return

        header = f"📜 *10 Transaksi Terakhir{' (' + ', '.join(akun_list) + ')' if akun_list else ''}*\n"
        lines = [header]
        for r in rows:
            arah = "⬆️" if r["debit"] > 0 else "⬇️"
            nominal = r["debit"] if r["debit"] > 0 else r["kredit"]
            lines.append(
                f"{arah} *{r['tanggal']}*\n"
                f"   {r['deskripsi']}\n"
                f"   {r['kategori']} | {r['akun']}\n"
                f"   Rp{nominal:,.2f}\n"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error riwayat: {e}")
        await update.message.reply_text(f"❌ Gagal ambil riwayat: {e}")


async def cmd_hari_ini(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Mengambil transaksi hari ini...")
    try:
        tanggal = today_str()
        rows = get_transaksi_hari_ini(tanggal)
        if not rows:
            await update.message.reply_text(f"Belum ada transaksi hari ini ({tanggal}).")
            return

        def _num(v):
            return float(str(v or 0).replace("Rp", "").replace(",", "").strip() or 0)

        total_masuk = 0.0
        total_keluar = 0.0
        n_neraca = 0

        lines = [f"📅 *Transaksi {tanggal}*\n"]
        for r in rows:
            debit = _num(r.get("Debit"))
            kredit = _num(r.get("Kredit"))
            kategori = str(r.get("Kategori", ""))
            tipe = category_type(kategori)
            arah = "⬆️" if debit > 0 else "⬇️"
            nominal = debit if debit > 0 else kredit
            tag = " ⏸️" if tipe in ("passthrough", "tabungan") else ""
            lines.append(
                f"{arah} {r.get('Deskripsi', '')}{tag}\n"
                f"   {kategori} | {r.get('Akun/Rekening', '')}\n"
                f"   Rp{nominal:,.2f}\n"
            )
            if tipe == "pemasukan":
                total_masuk += debit
            elif tipe == "pengeluaran":
                total_keluar += kredit
            else:
                n_neraca += 1

        footer = (
            f"─────────────────\n"
            f"⬆️ Pemasukan   : Rp{total_masuk:,.2f}\n"
            f"⬇️ Pengeluaran : Rp{total_keluar:,.2f}\n"
            f"📊 Net         : Rp{total_masuk - total_keluar:,.2f}"
        )
        if n_neraca:
            footer += f"\n⏸️ {n_neraca} transaksi pos-neraca (transfer/kas RT/hutang/titipan) tidak dihitung"
        lines.append(footer)
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error hari_ini: {e}")
        await update.message.reply_text(f"❌ Gagal ambil data hari ini: {e}")


def _fmt_items(items: list[dict], limit: int = 10) -> list[str]:
    """Format ringkas beberapa transaksi terakhir (terbaru dulu)."""
    lines = []
    for t in items[-limit:][::-1]:
        arah = "➕" if t["debit"] > 0 else "➖"
        nominal = t["debit"] if t["debit"] > 0 else t["kredit"]
        desc = t["deskripsi"][:32]
        lines.append(f"  {arah} {t['tanggal']} · {desc} · Rp{nominal:,.0f}")
    return lines


async def cmd_hutang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Mengambil posisi hutang...")
    try:
        total, rincian = get_hutang_from_dashboard()
        dash = get_dashboard_sheet_name()

        lines = ["💳 *Posisi Hutang*\n"]
        if total is None:
            lines.append(
                f"⚠️ Sel {dash}!K28 kosong/tak terbaca.\n"
                "Pastikan blok hutang terisi di dashboard (H23:M28)."
            )
        else:
            lines.append(f"*🔴 Sisa hutang: Rp{total:,.2f}*  _(sumber: {dash}!K28)_")

        rincian_aktif = [(p, s) for p, s in rincian if s > 0]
        if rincian_aktif:
            lines.append("\n_Rincian per pemberi:_")
            for pemberi, sisa in rincian_aktif:
                lines.append(f"  • {pemberi}: Rp{sisa:,.0f}")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error hutang: {e}")
        await update.message.reply_text(f"❌ Gagal ambil hutang: {e}")


async def cmd_piutang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Menghitung posisi piutang...")
    try:
        txns = get_all_transactions()
        items = [t for t in txns if t["kategori"].startswith("[Piutang]")]
        ditalangi = sum(t["kredit"] for t in items)  # [Piutang] Tambah (uang keluar dipinjamkan)
        kembali = sum(t["debit"] for t in items)       # [Piutang] Terima (dibayar balik)
        sisa = ditalangi - kembali

        lines = ["🤝 *Posisi Piutang*\n"]
        lines.append(f"➖ Ditalangi/dipinjamkan : Rp{ditalangi:,.2f}")
        lines.append(f"➕ Sudah dikembalikan    : Rp{kembali:,.2f}")
        lines.append(f"*🟢 Sisa piutang         : Rp{sisa:,.2f}*")
        if items:
            lines.append("\n_Rincian terbaru:_")
            lines += _fmt_items(items)
        else:
            lines.append("\n_Belum ada transaksi berkategori [Piutang]._")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error piutang: {e}")
        await update.message.reply_text(f"❌ Gagal hitung piutang: {e}")


async def cmd_kasrt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Merekonsiliasi kas RT...")
    try:
        txns = get_all_transactions()
        masuk = sum(t["debit"] for t in txns if t["kategori"].startswith("[Kas RT]"))
        keluar = sum(t["kredit"] for t in txns if t["kategori"].startswith("[Kas RT]"))
        net = masuk - keluar

        blu = None
        try:
            saldo, _, _ = get_saldo_dari_dashboard()
            blu = saldo.get("Blu Saving")
        except Exception:
            pass

        lines = ["🏘️ *Rekonsiliasi Kas RT*\n"]
        lines.append(f"📥 Kas RT masuk (ledger)  : Rp{masuk:,.2f}")
        lines.append(f"📤 Kas RT keluar (ledger) : Rp{keluar:,.2f}")
        lines.append(f"📊 Net kas RT (ledger)    : Rp{net:,.2f}")
        if blu is not None:
            lines.append(f"🏦 Saldo Blu Saving (fisik): Rp{blu:,.2f}")

        # Saldo buku bendahara: dari argumen bila ada, jika tidak baca dari dashboard M19
        if context.args:
            buku = parse_nominal(" ".join(context.args))
            buku_src = "input manual"
        else:
            buku = get_kas_rt_buku()
            buku_src = f"dashboard {get_dashboard_sheet_name()}!M19"

        if buku is None:
            if context.args:
                lines.append("\n⚠️ Angka buku bendahara tak terbaca. Contoh: `/kasrt 19584000`")
            else:
                lines.append(f"\n⚠️ Sel {get_dashboard_sheet_name()}!M19 kosong/tak terbaca. "
                             "Isi saldo buku di sel itu, atau kirim manual: `/kasrt 19584000`")
        elif blu is None:
            lines.append("\n⚠️ Saldo Blu Saving tak tersedia dari dashboard.")
        else:
            lubang = buku - blu
            lines.append(f"\n📒 Buku bendahara : Rp{buku:,.2f}  _(sumber: {buku_src})_")
            if abs(lubang) < 1:
                lines.append("✅ *Sesuai — tidak ada lubang.*")
            elif lubang > 0:
                lines.append(f"🔴 *LUBANG (kurang): Rp{lubang:,.2f}*")
            else:
                lines.append(f"🟢 *LEBIH: Rp{-lubang:,.2f}*")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error kasrt: {e}")
        await update.message.reply_text(f"❌ Gagal rekonsiliasi kas RT: {e}")


def _bar(frac: float, width: int = 10) -> str:
    frac = max(0.0, min(1.0, frac))
    filled = round(frac * width)
    return "▓" * filled + "░" * (width - filled)


async def cmd_anggaran(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Menghitung realisasi anggaran...")
    try:
        import calendar
        from datetime import datetime

        now = datetime.now(WIB)
        month_prefix = today_str()[:7]  # "YYYY/MM"
        dim = calendar.monthrange(now.year, now.month)[1]
        pace = now.day / dim

        txns = get_all_transactions()
        spent = {g: 0.0 for g in config.BUDGET_TARGETS}
        cicilan = 0.0
        for t in txns:
            if not str(t["tanggal"]).startswith(month_prefix):
                continue
            kat = t["kategori"]
            if category_type(kat) == "pengeluaran":
                for g in spent:
                    if kat.startswith(f"[{g}]"):
                        spent[g] += t["kredit"]
                        break
            elif kat == "[Hutang] Bayar":
                cicilan += t["kredit"]

        lines = [f"📊 *Anggaran {config.MONTH_ABBR[now.month]}* (hari ke-{now.day}/{dim})\n"]
        total_spent = 0.0
        total_target = 0.0
        for g, target in config.BUDGET_TARGETS.items():
            s = spent[g]
            total_spent += s
            total_target += target
            frac = s / target if target else 0.0
            sisa = target - s
            warn = " ⚠️" if s > target else (" 🔸" if frac > pace + 0.1 else "")
            lines.append(
                f"*{g}*  {_bar(frac)} {frac*100:.0f}%{warn}\n"
                f"   Rp{s:,.0f} / Rp{target:,.0f} · sisa Rp{sisa:,.0f}"
            )

        net_sisa = total_target - total_spent
        lines.append(
            f"─────────────────\n"
            f"Total belanja: Rp{total_spent:,.0f} / Rp{total_target:,.0f}\n"
            f"Sisa anggaran: Rp{net_sisa:,.0f}\n"
            f"Cicilan hutang bulan ini: Rp{cicilan:,.0f}"
        )
        lines.append(f"\n💡 Laju ideal ~{pace*100:.0f}% terpakai. 🔸=di atas laju, ⚠️=lewat target.")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error anggaran: {e}")
        await update.message.reply_text(f"❌ Gagal hitung anggaran: {e}")


async def cmd_batal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_state.pop(chat_id, None)
    await update.message.reply_text("❌ Transaksi dibatalkan.")


# ─── Message Handler ──────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    state = user_state.get(chat_id, {})
    stage = state.get("stage", "idle")

    # ── Stage: menunggu pilihan kategori ──────────────────────────────────────
    if stage == "awaiting_category":
        data = state["data"]
        choices = state["choices"]

        if text.lower() == "batal":
            user_state.pop(chat_id, None)
            await update.message.reply_text("❌ Transaksi dibatalkan.")
            return

        try:
            idx = int(text.strip()) - 1
            if 0 <= idx < len(choices):
                data["kategori"] = choices[idx]["kategori"]
                user_state[chat_id] = {"stage": "awaiting_confirm", "data": data}
                await update.message.reply_text(build_preview(data), parse_mode="Markdown")
            else:
                await update.message.reply_text(
                    f"Pilih angka 1–{len(choices)}, atau ketik *batal*.",
                    parse_mode="Markdown",
                )
        except ValueError:
            await update.message.reply_text(
                f"Ketik angka 1–{len(choices)} untuk memilih kategori, atau *batal*.",
                parse_mode="Markdown",
            )
        return

    # ── Stage: menunggu konfirmasi transaksi biasa ────────────────────────────
    if stage == "awaiting_confirm":
        data = state["data"]

        if text.lower() == "ok":
            try:
                debit = data["nominal_float"] if data["jenis"] == "masuk" else 0.0
                kredit = data["nominal_float"] if data["jenis"] == "keluar" else 0.0
                new_saldo = append_transaction(
                    tanggal=data["tanggal"],
                    deskripsi=data["deskripsi"],
                    kategori=data["kategori"],
                    akun=data["akun"],
                    debit=debit,
                    kredit=kredit,
                )
                user_state.pop(chat_id, None)
                await update.message.reply_text(
                    f"✅ *Tersimpan!*\n\n"
                    f"💰 Saldo total terkini: *Rp{new_saldo:,.2f}*\n\n"
                    f"Ketik /saldo untuk rekap lengkap per akun.",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.error(f"Error simpan transaksi: {e}")
                await update.message.reply_text(f"❌ Gagal menyimpan: {e}")

        elif text.lower() == "batal":
            user_state.pop(chat_id, None)
            await update.message.reply_text("❌ Transaksi dibatalkan.")
        else:
            await update.message.reply_text(
                "Balas *ok* untuk simpan atau *batal* untuk membatalkan.",
                parse_mode="Markdown",
            )
        return

    # ── Stage: menunggu konfirmasi transfer ───────────────────────────────────
    if stage == "awaiting_transfer_confirm":
        data = state["data"]

        if text.lower() == "ok":
            try:
                new_saldo = append_transfer(
                    tanggal=data["tanggal"],
                    akun_asal=data["dari"],
                    akun_tujuan=data["ke"],
                    nominal=data["nominal_float"],
                    kode_unik=data.get("kode_unik_float", 0.0),
                )
                user_state.pop(chat_id, None)
                await update.message.reply_text(
                    f"✅ *Transfer tersimpan!*\n\n"
                    f"💰 Saldo total terkini: *Rp{new_saldo:,.2f}*\n\n"
                    f"Ketik /saldo untuk rekap lengkap per akun.",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.error(f"Error simpan transfer: {e}")
                await update.message.reply_text(f"❌ Gagal menyimpan: {e}")

        elif text.lower() == "batal":
            user_state.pop(chat_id, None)
            await update.message.reply_text("❌ Transfer dibatalkan.")
        else:
            await update.message.reply_text(
                "Balas *ok* untuk simpan atau *batal* untuk membatalkan.",
                parse_mode="Markdown",
            )
        return

    # ── Stage: idle — deteksi jenis pesan ─────────────────────────────────────
    if "=" not in text:
        return

    # Tentukan jenis dari field "jenis ="
    jenis_raw = ""
    for line in text.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            if k.strip().lower() == "jenis":
                jenis_raw = v.strip().lower()
                break

    # Routing: transfer vs transaksi biasa
    if jenis_raw == "transfer":
        transfer = parse_transfer_message(text)
        if isinstance(transfer, str):
            await update.message.reply_text(f"⚠️ {transfer}", parse_mode="Markdown")
            return

        akun_asal = match_account(transfer["dari"])
        akun_tujuan = match_account(transfer["ke"])

        if akun_asal is None:
            await update.message.reply_text(
                f"⚠️ Akun asal *{transfer['dari']}* tidak dikenali.", parse_mode="Markdown"
            )
            return
        if akun_tujuan is None:
            await update.message.reply_text(
                f"⚠️ Akun tujuan *{transfer['ke']}* tidak dikenali.", parse_mode="Markdown"
            )
            return
        if akun_asal == akun_tujuan:
            await update.message.reply_text("⚠️ Akun asal dan tujuan tidak boleh sama.")
            return

        transfer["dari"] = akun_asal
        transfer["ke"] = akun_tujuan
        user_state[chat_id] = {"stage": "awaiting_transfer_confirm", "data": transfer}
        await update.message.reply_text(build_transfer_preview(transfer), parse_mode="Markdown")
        return

    # Parse sebagai transaksi biasa
    parsed = parse_transaction_message(text)

    if isinstance(parsed, str):
        await update.message.reply_text(
            f"⚠️ Format tidak valid: {parsed}\n\n"
            "Gunakan /format untuk melihat template.",
            parse_mode="Markdown",
        )
        return

    akun_valid = match_account(parsed["akun"])
    if akun_valid is None:
        await update.message.reply_text(
            f"⚠️ Akun *{parsed['akun']}* tidak dikenali.\n"
            "Gunakan /saldo untuk melihat daftar akun yang tersedia.",
            parse_mode="Markdown",
        )
        return
    parsed["akun"] = akun_valid

    # Prefix single/directional (Income, Kas RT, Hutang, Piutang, Bisnis, Adek, Transfer, Lain)
    # ditentukan langsung dari prefix + jenis, tanpa fuzzy matching.
    prefix_std = resolve_prefix(parsed["prefix"])
    forced = resolve_forced_category(prefix_std, parsed["jenis"])
    if forced:
        parsed["kategori"] = forced
        user_state[chat_id] = {"stage": "awaiting_confirm", "data": parsed}
        await update.message.reply_text(build_preview(parsed), parse_mode="Markdown")
        return

    kategori = best_match(parsed["prefix"], parsed["deskripsi"], threshold=0.8)
    if kategori:
        parsed["kategori"] = kategori
        user_state[chat_id] = {"stage": "awaiting_confirm", "data": parsed}
        await update.message.reply_text(build_preview(parsed), parse_mode="Markdown")
    else:
        choices = match_category(parsed["prefix"], parsed["deskripsi"], top_n=3)
        choices_text = format_category_choices(choices)
        user_state[chat_id] = {"stage": "awaiting_category", "data": parsed, "choices": choices}
        await update.message.reply_text(
            f"🤔 Kategori tidak ditemukan secara otomatis.\n"
            f"Pilih yang paling sesuai (ketik angkanya):\n\n"
            f"{choices_text}\n\n"
            f"Atau ketik *batal* untuk membatalkan.",
            parse_mode="Markdown",
        )


# ─── Main ─────────────────────────────────────────────────────────────────────

def _load_master_categories():
    """Best-effort: muat kategori + tipe dari sheet Data Master, timpa default config."""
    try:
        master = get_master_categories()
        if master:
            # Union daftar kategori; tipe kategori yang sudah dikenal config diprioritaskan
            # (agar [Income]/[Bisnis] tak salah klasifikasi bila sheet tak punya kolom Tipe).
            merged = {**master, **config.CATEGORY_TYPES}
            config.CATEGORY_TYPES = merged
            config.VALID_CATEGORIES = list(merged.keys())
            logger.info(f"Loaded {len(master)} kategori dari sheet '{config.MASTER_SHEET_NAME}'.")
        else:
            logger.info("Sheet Data Master tak terbaca; pakai kategori bawaan config.")
    except Exception as e:
        logger.warning(f"Gagal load Data Master ({e}); pakai kategori bawaan config.")


def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    _load_master_categories()
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("saldo", cmd_saldo))
    app.add_handler(CommandHandler("format", cmd_format))
    app.add_handler(CommandHandler("transfer", cmd_transfer))
    app.add_handler(CommandHandler("riwayat", cmd_riwayat))
    app.add_handler(CommandHandler("hari_ini", cmd_hari_ini))
    app.add_handler(CommandHandler("hutang", cmd_hutang))
    app.add_handler(CommandHandler("piutang", cmd_piutang))
    app.add_handler(CommandHandler("kasrt", cmd_kasrt))
    app.add_handler(CommandHandler("anggaran", cmd_anggaran))
    app.add_handler(CommandHandler("batal", cmd_batal))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Fintrack Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()