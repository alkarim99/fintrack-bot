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
from config import today_str
from matcher import match_category, best_match, match_account, format_category_choices
from sheets import (
    append_transaction,
    append_transfer,
    get_saldo_dari_dashboard,
    get_riwayat,
    get_transaksi_hari_ini,
    format_saldo_rekap,
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
    raw = raw.strip().lower().replace(",", "").replace(".", "")
    multiplier = 1
    if raw.endswith("rb") or raw.endswith("k"):
        multiplier = 1_000
        raw = re.sub(r"(rb|k)$", "", raw)
    elif raw.endswith("jt") or raw.endswith("m"):
        multiplier = 1_000_000
        raw = re.sub(r"(jt|m)$", "", raw)
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

    if result.get("jenis", "").lower() != "transfer":
        return None  # bukan pesan transfer, lanjut ke parse biasa

    nominal = parse_nominal(result["nominal"])
    if nominal is None or nominal <= 0:
        return f"Nominal '{result['nominal']}' tidak bisa dibaca."
    result["nominal_float"] = nominal

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
        f"{jenis_label} : Rp{data['nominal_float']:,.0f}\n\n"
        f"Balas *ok* untuk simpan, atau *batal* untuk membatalkan."
    )


def build_transfer_preview(data: dict) -> str:
    if data["ke"].lower() == "cash":
        deskripsi = f"Tarik tunai dari {data['dari']}"
    else:
        deskripsi = f"{data['dari']} → {data['ke']}"
    return (
        f"📋 *Preview Transfer Internal*\n\n"
        f"📅 Tanggal : {data['tanggal']}\n"
        f"↔️ Transfer : {deskripsi}\n"
        f"💸 Nominal  : Rp{data['nominal_float']:,.0f}\n\n"
        f"Akan dicatat sebagai *2 baris* di Transaction Log.\n\n"
        f"Balas *ok* untuk simpan, atau *batal* untuk membatalkan."
    )


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
        "  /transfer — Template input transfer internal\n\n"
        "📊 *Laporan*\n"
        "  /saldo — Rekap saldo per akun\n"
        "  /riwayat — 10 transaksi terakhir\n"
        "  /hari\\_ini — Ringkasan transaksi hari ini\n\n"
        "⚙️ *Lainnya*\n"
        "  /batal — Batalkan input yang sedang berjalan\n"
        "  /help — Tampilkan pesan ini\n\n"
        "💡 Atau langsung kirim transaksi tanpa command menggunakan format dari /format",
        parse_mode="Markdown",
    )


async def cmd_saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Mengambil data saldo...")
    try:
        saldo = get_saldo_dari_dashboard()
        rekap = format_saldo_rekap(saldo)
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
        "📌 *Prefix:* kakak / pokok / adek\n"
        "📌 *Nominal:* 15rb, 1.5jt, 500000\n"
        "📌 *Tanggal (opsional):* tambahkan `tanggal = 25/04/2026` jika bukan hari ini",
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
        "```\n\n"
        "💡 *Contoh:*\n"
        "```\n"
        "jenis = transfer\n"
        "dari = BCA\n"
        "ke = Jago Main\n"
        "nominal = 500rb\n"
        "```\n\n"
        "💡 *Contoh tarik tunai:*\n"
        "```\n"
        "jenis = transfer\n"
        "dari = BCA\n"
        "ke = Cash\n"
        "nominal = 200rb\n"
        "```\n\n"
        "📌 *Tanggal (opsional):* tambahkan `tanggal = 25/04/2026` jika bukan hari ini",
        parse_mode="Markdown",
    )


async def cmd_riwayat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Mengambil riwayat transaksi...")
    try:
        rows = get_riwayat(n=10)
        if not rows:
            await update.message.reply_text("Belum ada transaksi.")
            return

        lines = ["📜 *10 Transaksi Terakhir*\n"]
        for r in rows:
            arah = "⬆️" if r["debit"] > 0 else "⬇️"
            nominal = r["debit"] if r["debit"] > 0 else r["kredit"]
            lines.append(
                f"{arah} *{r['tanggal']}*\n"
                f"   {r['deskripsi']}\n"
                f"   {r['kategori']} | {r['akun']}\n"
                f"   Rp{nominal:,.0f}\n"
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

        total_masuk = sum(float(str(r.get("Debit", 0) or 0).replace(",", "")) for r in rows)
        total_keluar = sum(float(str(r.get("Kredit", 0) or 0).replace(",", "")) for r in rows)

        lines = [f"📅 *Transaksi {tanggal}*\n"]
        for r in rows:
            debit = float(str(r.get("Debit", 0) or 0).replace(",", ""))
            kredit = float(str(r.get("Kredit", 0) or 0).replace(",", ""))
            arah = "⬆️" if debit > 0 else "⬇️"
            nominal = debit if debit > 0 else kredit
            lines.append(
                f"{arah} {r.get('Deskripsi', '')}\n"
                f"   {r.get('Kategori', '')} | {r.get('Akun/Rekening', '')}\n"
                f"   Rp{nominal:,.0f}\n"
            )

        lines.append(
            f"─────────────────\n"
            f"⬆️ Total Masuk  : Rp{total_masuk:,.0f}\n"
            f"⬇️ Total Keluar : Rp{total_keluar:,.0f}\n"
            f"📊 Net          : Rp{total_masuk - total_keluar:,.0f}"
        )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error hari_ini: {e}")
        await update.message.reply_text(f"❌ Gagal ambil data hari ini: {e}")


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
                    f"💰 Saldo total terkini: *Rp{new_saldo:,.0f}*\n\n"
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
                )
                user_state.pop(chat_id, None)
                await update.message.reply_text(
                    f"✅ *Transfer tersimpan!*\n\n"
                    f"💰 Saldo total terkini: *Rp{new_saldo:,.0f}*\n\n"
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

def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("saldo", cmd_saldo))
    app.add_handler(CommandHandler("format", cmd_format))
    app.add_handler(CommandHandler("transfer", cmd_transfer))
    app.add_handler(CommandHandler("riwayat", cmd_riwayat))
    app.add_handler(CommandHandler("hari_ini", cmd_hari_ini))
    app.add_handler(CommandHandler("batal", cmd_batal))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Fintrack Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()