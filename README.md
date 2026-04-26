# Fintrack Bot

Telegram bot untuk mencatat transaksi keuangan ke Google Sheets secara otomatis.

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
5. Paste email service account tadi → ubah role ke **Editor** → klik **Send**
6. Salin **Spreadsheet ID** dari URL:
   ```
   https://docs.google.com/spreadsheets/d/1jazkLa6ie_TlSQ2OelqTQByOcld5wfRH-FM9SVkNUCE/edit
   ```
---

### Step 4 — Konfigurasi Environment

1. Salin file contoh:
   ```bash
   cp .env.example .env
   ```
2. Edit file `.env`:
   ```
   TELEGRAM_BOT_TOKEN=token_dari_botfather
   SPREADSHEET_ID=id_dari_url_spreadsheet
   GOOGLE_CREDENTIALS_PATH=credentials.json
   ```

---

### Step 5 — Sesuaikan Nama Sheet (Penting!)

Buka `config.py` dan pastikan nama sheet sesuai dengan tab di spreadsheet Anda:

```python
TRANSACTION_SHEET_NAME = "Transaction Log"  # sesuaikan dengan nama tab
```

---

### Step 6 — Install & Jalankan

```bash
# Install dependencies
pip install -r requirements.txt

# Jalankan bot
python bot.py
```

---

## Cara Pakai

### Catat Transaksi

Kirim pesan ke bot dengan format:

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

**Format nominal yang didukung:** `35rb`, `1.5jt`, `500000`, `1,500,000`

### Alur Konfirmasi

```
Anda  → kirim format transaksi
Bot   → tampilkan preview atau pilihan kategori
Anda  → ketik nomor (jika ada pilihan kategori)
Bot   → tampilkan preview final
Anda  → ok (simpan) atau batal (batalkan)
Bot   → ✅ tersimpan + saldo total terkini
```

### Perintah Lain

| Perintah | Fungsi |
|---|---|
| `/saldo` | Rekap saldo per akun |
| `/batal` | Batalkan transaksi yang sedang diproses |
| `/start` | Tampilkan panduan singkat |

---

## Deploy ke Railway (Gratis)

1. Push kode ini ke GitHub (jangan include `credentials.json` dan `.env`)
2. Buka [https://railway.app](https://railway.app) → login dengan GitHub
3. **New Project** → **Deploy from GitHub repo** → pilih repo ini
4. Tambahkan environment variables di Railway:
   - `TELEGRAM_BOT_TOKEN`
   - `SPREADSHEET_ID`
   - `GOOGLE_CREDENTIALS_PATH` → isi `credentials.json`
5. Upload `credentials.json` sebagai file di Railway (via **Files** tab atau set isinya sebagai env var `GOOGLE_CREDENTIALS_JSON`)

> **Tip:** Daripada upload file, lebih aman menyimpan isi credentials.json sebagai environment variable `GOOGLE_CREDENTIALS_JSON`, lalu modifikasi `sheets.py` untuk membacanya dari env var.

---

## Struktur File

```
fintrack-bot/
├── bot.py              ← main bot & telegram handler
├── matcher.py          ← logika pencocokan kategori
├── sheets.py           ← integrasi Google Sheets
├── config.py           ← daftar kategori, akun, konstanta
├── requirements.txt
├── .env.example
└── credentials.json    ← JANGAN di-commit ke git!
```
