# Fintrack Bot

Telegram bot untuk mencatat transaksi keuangan ke Google Sheets secara otomatis.

---

## Fitur

- Catat transaksi masuk/keluar via Telegram
- Catat transfer internal antar rekening (2 baris otomatis)
- Pencocokan kategori otomatis (fuzzy matching + keyword hints)
- Preview sebelum simpan
- Tanggal opsional (default hari ini)
- `/saldo` — rekap saldo per akun dari dashboard
- `/riwayat` — 10 transaksi terakhir
- `/hari_ini` — ringkasan transaksi hari ini
- `/format` — template transaksi siap copas
- `/transfer` — template transfer internal siap copas

---

## Setup (Langkah demi Langkah)

### Step 1 — Buat Telegram Bot

1. Buka Telegram → cari **@BotFather**
2. Ketik `/newbot`
3. Masukkan nama bot, misal: `Fintrack`
4. Masukkan username bot, misal: `fintrack_karim_bot`
5. Salin **token** yang diberikan, contoh: `7123456789:ABCdef...`

---

### Step 2 — Setup Google Cloud & Service Account

1. Buka [https://console.cloud.google.com](https://console.cloud.google.com)
2. Klik **Select a project** → **New Project** → beri nama misal `fintrack-bot` → **Create**
3. Di sidebar, buka **APIs & Services** → **Enable APIs and Services**
4. Cari dan aktifkan:
   - **Google Sheets API** → Enable
   - **Google Drive API** → Enable
5. Kembali ke **APIs & Services** → **Credentials**
6. Klik **+ Create Credentials** → **Service Account**
7. Isi nama, misal `fintrack-service` → **Create and Continue** → **Done**
8. Klik service account yang baru dibuat → tab **Keys**
9. **Add Key** → **Create new key** → pilih **JSON** → **Create**
10. File JSON akan otomatis terunduh — simpan sebagai `credentials.json` di folder project ini

---

### Step 3 — Bagikan Spreadsheet ke Service Account

1. Buka file `credentials.json` yang baru diunduh
2. Salin nilai `"client_email"`, contoh: `fintrack-service@fintrack-bot.iam.gserviceaccount.com`
3. Buka Google Sheets Fintrack Anda
4. Klik tombol **Share** (pojok kanan atas)
5. Paste email service account → ubah role ke **Editor** → klik **Send**
6. Salin **Spreadsheet ID** dari URL:
   ```
   https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit
   ```

---

### Step 4 — Konfigurasi Environment

Salin file contoh:
```bash
cp .env.example .env
```

Edit file `.env`:
```
TELEGRAM_BOT_TOKEN=token_dari_botfather
SPREADSHEET_ID=id_dari_url_spreadsheet
GOOGLE_CREDENTIALS_PATH=credentials.json
```

> **Untuk server/cloud:** gunakan `GOOGLE_CREDENTIALS_JSON` berisi isi lengkap `credentials.json` sebagai satu baris JSON, tanpa perlu upload file.

---

### Step 5 — Sesuaikan Nama Sheet

Buka `config.py` dan pastikan nama sheet sesuai dengan tab di spreadsheet Anda:

```python
TRANSACTION_SHEET_NAME = "Transaction Log"  # nama tab transaction log
```

Format nama sheet dashboard: `Dash-JAN`, `Dash-FEB`, ..., `Dash-DEC` — otomatis sesuai bulan berjalan.

---

### Step 6 — Install & Jalankan

```bash
# Buat virtual environment
python3 -m venv venv
source venv/bin/activate  # Mac/Linux

# Install dependencies
pip install -r requirements.txt

# Jalankan bot
python bot.py
```

Untuk menjalankan di background (home server / PC):
```bash
nohup python bot.py &> bot.log &

# Cek log
tail -f bot.log

# Stop bot
pkill -f bot.py
```

---

## Cara Pakai

### Catat Transaksi

Gunakan `/format` untuk template, atau langsung kirim:

```
jenis = keluar
prefix = kakak
deskripsi = bayar makan penyetan bu ovi
akun = BCA
nominal = 15rb
```

```
jenis = masuk
prefix = kakak
deskripsi = terima gaji
akun = Mandiri
nominal = 8jt
```

**Prefix yang tersedia:** `kakak`, `pokok`, `adek`, `lain`, `titipan`

**Format nominal:** `35rb`, `1.5jt`, `500000`, `1,500,000`

**Tanggal opsional:** tambahkan `tanggal = 25/04/2026` jika bukan hari ini

### Catat Transfer Internal

Gunakan `/transfer` untuk template, atau langsung kirim:

```
jenis = transfer
dari = BCA
ke = Jago Main
nominal = 500rb
```

Tarik tunai (tujuan ke Cash):
```
jenis = transfer
dari = BCA
ke = Cash
nominal = 200rb
```

Bot otomatis mencatat 2 baris: kredit di akun asal + debit di akun tujuan.

### Alur Konfirmasi

```
Anda  → kirim format transaksi
Bot   → preview atau pilihan kategori (jika ambigu)
Anda  → ketik nomor (jika ada pilihan)
Bot   → preview final
Anda  → ok (simpan) atau batal (batalkan)
Bot   → ✅ tersimpan + saldo terkini
```

---

## Daftar Command

| Command | Fungsi |
|---|---|
| `/help` | Daftar semua command |
| `/format` | Template transaksi siap copas |
| `/transfer` | Template transfer internal siap copas |
| `/saldo` | Rekap saldo per akun dari dashboard |
| `/riwayat` | 10 transaksi terakhir |
| `/hari_ini` | Ringkasan transaksi hari ini |
| `/batal` | Batalkan input yang sedang berjalan |

---

## Struktur File

```
fintrack-bot/
├── bot.py              ← main bot & telegram handler
├── matcher.py          ← logika pencocokan kategori (fuzzy + keyword hints)
├── sheets.py           ← integrasi Google Sheets
├── config.py           ← kategori, akun, konstanta, prefix map
├── requirements.txt
├── Procfile            ← untuk deploy (Railway, Fly.io, dll)
├── .env.example
├── .gitignore
└── credentials.json    ← JANGAN di-commit ke git!
```

---

## Deploy ke Home Server

```bash
# Clone repo
git clone https://github.com/username/fintrack-bot.git
cd fintrack-bot

# Setup environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Copy credentials (sekali saja)
cp /path/to/credentials.json .

# Buat .env
cp .env.example .env
# Edit .env dengan token dan spreadsheet ID

# Jalankan
nohup python bot.py &> bot.log &
```

Untuk auto-start saat server reboot, tambahkan ke crontab:
```bash
crontab -e
# Tambahkan baris:
@reboot cd /path/to/fintrack-bot && source venv/bin/activate && nohup python bot.py &> bot.log &
```