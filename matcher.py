from difflib import SequenceMatcher
import config  # referensi dinamis agar daftar dari Data Master (startup) ikut terpakai

# Keyword → kata kunci di nama kategori (untuk boost scoring)
KEYWORD_HINTS = {
    "listrik": "token listrik",
    "token": "token listrik",
    "pertamax": "bensin",
    "pertalite": "bensin",
    "solar": "bensin",
    "bensin": "bensin",
    "internet": "paket internet",
    "pulsa": "paket internet",
    "paketan": "paket internet",
    "makan": "bayar makan",
    "kopi": "bayar makan",
    "kos": "kos",
    "sembako": "sembako",
    "sabun": "sembako",
    "galon": "galon",
    "sedekah": "sedekah",
    "infaq": "sedekah",
    "odot": "sedekah",
    "zakat": "zakat",
    "parkir": "transport",
    "bus": "transport",
    "tiket": "transport",
    "transport": "transport",
    "servis": "kendaraan",
    "service": "kendaraan",
    "kendaraan": "kendaraan",
    "oli": "kendaraan",
    "ban": "kendaraan",
    "tabungan": "tabungan",
    "nabung": "tabungan",
    "pakaian": "pakaian",
    "baju": "pakaian",
    "admin": "biaya admin",
    "transfer": "internal",
    "besmart": "besmart",
    "apotek": "apotek",
    "obat": "apotek",
    "iuran": "bayar iuran",
    "speedy": "speedy",
    "claude": "subscription ai",
    "chatgpt": "subscription ai",
    "gemini": "subscription ai",
    "deepseek": "subscription ai",
    "spotify": "subscription ai",
    "subscription": "subscription ai",
    "tua": "kirim ke orang tua",
    "ortu": "kirim ke orang tua",
    "orangtua": "kirim ke orang tua",
    "modal": "modal",
    "dagang": "modal",
}


def resolve_prefix(prefix_input: str) -> str:
    """Normalisasi input prefix ke prefix standar, dukung multi-kata (mis. 'kas rt' → 'Kas RT')."""
    raw = prefix_input.strip().lower()
    if raw in config.PREFIX_MAP:
        return config.PREFIX_MAP[raw]
    tokens = raw.split()
    if len(tokens) >= 2 and " ".join(tokens[:2]) in config.PREFIX_MAP:
        return config.PREFIX_MAP[" ".join(tokens[:2])]
    first = tokens[0] if tokens else raw
    return config.PREFIX_MAP.get(first, first.capitalize())


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _extract_prefix_and_hint(prefix_input: str) -> tuple[str, str]:
    """
    Pisahkan prefix dan hint kategori dari input prefix.
    Contoh: "Kakak Income" → ("Kakak", "income")
    """
    prefix_std = resolve_prefix(prefix_input)
    parts = prefix_input.strip().split(None, 1)
    hint = parts[1].lower() if len(parts) > 1 else ""
    return prefix_std, hint


def match_category(prefix_input: str, deskripsi: str, top_n: int = 3) -> list[dict]:
    prefix_std, hint = _extract_prefix_and_hint(prefix_input)

    search_text = f"{hint} {deskripsi}".strip().lower()

    # Kumpulkan kata kunci dari KEYWORD_HINTS yang cocok dengan search_text
    keyword_boosts = set()
    for word in search_text.split():
        if word in KEYWORD_HINTS:
            keyword_boosts.add(KEYWORD_HINTS[word].lower())

    scores = []
    for cat in config.VALID_CATEGORIES:
        cat_lower = cat.lower()
        cat_name = cat_lower.split("]", 1)[-1].strip() if "]" in cat_lower else cat_lower

        score = _similarity(search_text, cat_name)

        # Bonus prefix cocok
        if f"[{prefix_std}]" in cat:
            score += 0.6

        # Bonus keyword langsung muncul di cat_name
        for word in search_text.split():
            if len(word) > 2 and word in cat_name:
                score += 0.25
                break

        # Bonus dari KEYWORD_HINTS (persis = nama kategori → sinyal kuat)
        kw_bonus = 0.0
        for boost_kw in keyword_boosts:
            if boost_kw == cat_name:
                kw_bonus = max(kw_bonus, 0.8)
            elif boost_kw in cat_name:
                kw_bonus = max(kw_bonus, 0.4)
        score += kw_bonus

        scores.append({"kategori": cat, "score": round(score, 3)})

    scores.sort(key=lambda x: x["score"], reverse=True)
    return scores[:top_n]


def best_match(prefix_input: str, deskripsi: str, threshold: float = 0.8) -> str | None:
    results = match_category(prefix_input, deskripsi, top_n=1)
    if results and results[0]["score"] >= threshold:
        return results[0]["kategori"]
    return None


def match_account(akun_input: str) -> str | None:
    akun_lower = akun_input.strip().lower()
    for acc in config.VALID_ACCOUNTS:
        if acc.lower() == akun_lower:
            return acc
    scores = [(acc, _similarity(akun_lower, acc.lower())) for acc in config.VALID_ACCOUNTS]
    scores.sort(key=lambda x: x[1], reverse=True)
    if scores and scores[0][1] >= 0.7:
        return scores[0][0]
    return None


def format_category_choices(matches: list[dict]) -> str:
    lines = []
    for i, m in enumerate(matches, start=1):
        lines.append(f"  {i}. {m['kategori']}")
    return "\n".join(lines)