"""Utilitaires réseau, dates, drapeaux FIDE et écriture JSON (Python 3.11 stdlib)."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

UA = "EchecsDashboard/1.0 (+https://github.com/Camelbaggy/Echecs)"
PARIS_TZ = ZoneInfo("Europe/Paris")

# Correspondance code fédération FIDE (3 lettres) -> code drapeau flagcdn.com (ISO2 ou gb-*)
# FID = drapeau neutre FIDE (pas d'image flagcdn).
FIDE_TO_ISO: dict[str, str] = {
    "AFG": "af", "ALB": "al", "ALG": "dz", "AND": "ad", "ANG": "ao", "ARG": "ar",
    "ARM": "am", "AUS": "au", "AUT": "at", "AZE": "az", "BAN": "bd", "BAR": "bb",
    "BEL": "be", "BIH": "ba", "BLR": "by", "BOL": "bo", "BOT": "bw", "BRA": "br",
    "BUL": "bg", "CAN": "ca", "CHI": "cl", "CHN": "cn", "COL": "co", "CRC": "cr",
    "CRO": "hr", "CUB": "cu", "CYP": "cy", "CZE": "cz", "DEN": "dk", "DOM": "do",
    "ECU": "ec", "EGY": "eg", "ENG": "gb-eng", "ESA": "sv", "ESP": "es", "EST": "ee",
    "ETH": "et", "FAI": "fo", "FID": "", "FIN": "fi", "FRA": "fr", "GEO": "ge",
    "GER": "de", "GHA": "gh", "GRE": "gr", "GUA": "gt", "HKG": "hk", "HON": "hn",
    "HUN": "hu", "INA": "id", "IND": "in", "IRI": "ir", "IRL": "ie", "IRQ": "iq",
    "ISL": "is", "ISR": "il", "ITA": "it", "JAM": "jm", "JOR": "jo", "JPN": "jp",
    "KAZ": "kz", "KEN": "ke", "KGZ": "kg", "KOR": "kr", "KOS": "xk", "KSA": "sa",
    "KUW": "kw", "LAT": "lv", "LBA": "ly", "LBN": "lb", "LIE": "li", "LTU": "lt",
    "LUX": "lu", "MAC": "mo", "MAD": "mg", "MAR": "ma", "MAS": "my", "MDA": "md",
    "MEX": "mx", "MGL": "mn", "MKD": "mk", "MLT": "mt", "MNC": "mc", "MNE": "me",
    "MRI": "mu", "MYA": "mm", "NAM": "na", "NCA": "ni", "NED": "nl", "NEP": "np",
    "NGR": "ng", "NOR": "no", "NZL": "nz", "OMA": "om", "PAK": "pk", "PAN": "pa",
    "PAR": "py", "PER": "pe", "PHI": "ph", "PLE": "ps", "POL": "pl", "POR": "pt",
    "PUR": "pr", "QAT": "qa", "ROU": "ro", "RSA": "za", "RUS": "ru", "SCO": "gb-sct",
    "SEN": "sn", "SGP": "sg", "SLO": "si", "SRB": "rs", "SRI": "lk", "SUD": "sd",
    "SUI": "ch", "SVK": "sk", "SWE": "se", "SYR": "sy", "THA": "th", "TJK": "tj",
    "TKM": "tm", "TPE": "tw", "TTO": "tt", "TUN": "tn", "TUR": "tr", "UAE": "ae",
    "UGA": "ug", "UKR": "ua", "URU": "uy", "USA": "us", "UZB": "uz", "VEN": "ve",
    "VIE": "vn", "WLS": "gb-wls", "YEM": "ye", "ZAM": "zm", "ZIM": "zw",
}

FIDE_PAYS_FR: dict[str, str] = {
    "ARG": "Argentine", "ARM": "Arménie", "AUS": "Australie", "AUT": "Autriche",
    "AZE": "Azerbaïdjan", "BEL": "Belgique", "BRA": "Brésil", "BUL": "Bulgarie",
    "CAN": "Canada", "CHN": "Chine", "CRO": "Croatie", "CUB": "Cuba",
    "CZE": "Tchéquie", "DEN": "Danemark", "EGY": "Égypte", "ENG": "Angleterre",
    "ESP": "Espagne", "EST": "Estonie", "FID": "FIDE", "FIN": "Finlande",
    "FRA": "France", "GEO": "Géorgie", "GER": "Allemagne", "GRE": "Grèce",
    "HUN": "Hongrie", "IND": "Inde", "IRI": "Iran", "IRL": "Irlande",
    "ISL": "Islande", "ISR": "Israël", "ITA": "Italie", "JPN": "Japon",
    "KAZ": "Kazakhstan", "LAT": "Lettonie", "LTU": "Lituanie", "LUX": "Luxembourg",
    "MAR": "Maroc", "MDA": "Moldavie", "MEX": "Mexique", "MNC": "Monaco",
    "MNE": "Monténégro", "NED": "Pays-Bas", "NOR": "Norvège", "PER": "Pérou",
    "PHI": "Philippines", "POL": "Pologne", "POR": "Portugal", "ROU": "Roumanie",
    "RUS": "Russie", "SCO": "Écosse", "SGP": "Singapour", "SLO": "Slovénie",
    "SRB": "Serbie", "SUI": "Suisse", "SVK": "Slovaquie", "SWE": "Suède",
    "TUN": "Tunisie", "TUR": "Turquie", "UAE": "Émirats arabes unis",
    "UKR": "Ukraine", "USA": "États-Unis", "UZB": "Ouzbékistan", "VIE": "Viêt Nam",
    "WLS": "Pays de Galles",
}

MOIS_FR = [
    "", "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

_LAST_HOST_CALL: dict[str, float] = {}


def fide_flag(fed: str | None) -> str:
    """Retourne le code flagcdn.com pour une fédération FIDE, ou '' si inconnu / FID."""
    if not fed:
        return ""
    return FIDE_TO_ISO.get(fed.strip().upper(), "")


def fide_pays(fed: str | None) -> str:
    """Retourne le nom français du pays pour une fédération FIDE."""
    if not fed:
        return ""
    code = fed.strip().upper()
    return FIDE_PAYS_FR.get(code, code)


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def paris_now() -> dt.datetime:
    return dt.datetime.now(PARIS_TZ)


def date_fr(iso_date: str) -> str:
    """Convertit '2026-11-26' en '26 novembre 2026'."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", iso_date or "")
    if not m:
        return iso_date or ""
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if 1 <= mo <= 12:
        jour = "1er" if d == 1 else str(d)
        return f"{jour} {MOIS_FR[mo]} {y}"
    return iso_date


def normalize(text: str) -> str:
    s = unicodedata.normalize("NFKD", text or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower()).strip()


def slugify(text: str) -> str:
    s = normalize(text)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "item"


def fetch_bytes(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    retries: int = 3,
    min_interval: float = 0.4,
    pause_429: float = 60.0,
) -> bytes:
    """Télécharge une URL avec respect du débit par hôte et reprise sur 429 / 5xx."""
    host = urllib.request.urlparse(url).netloc
    hdrs = {"User-Agent": UA}
    if headers:
        hdrs.update(headers)

    last_err: Exception | None = None
    for attempt in range(retries):
        now = time.monotonic()
        prev = _LAST_HOST_CALL.get(host, 0.0)
        wait = min_interval - (now - prev)
        if wait > 0:
            time.sleep(wait)
        _LAST_HOST_CALL[host] = time.monotonic()

        req = urllib.request.Request(url, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429:
                ra = e.headers.get("Retry-After")
                delay = float(ra) if ra and ra.isdigit() else pause_429
                delay = min(max(delay, 2.0), 120.0)
                print(f"[HTTP 429] {host} — pause {delay:.0f}s (essai {attempt + 1}/{retries})")
                if attempt + 1 < retries:
                    time.sleep(delay)
                    continue
            elif 500 <= e.code < 600 and attempt + 1 < retries:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            if attempt + 1 < retries:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise
    assert last_err is not None
    raise last_err


def fetch_json(url: str, **kwargs) -> object:
    raw = fetch_bytes(url, **kwargs)
    return json.loads(raw.decode("utf-8"))


def fetch_text(url: str, encoding: str = "utf-8", **kwargs) -> str:
    raw = fetch_bytes(url, **kwargs)
    return raw.decode(encoding, errors="replace")


def _strip_ignored(obj: object, ignore_keys: tuple[str, ...]) -> object:
    if isinstance(obj, dict):
        return {k: _strip_ignored(v, ignore_keys) for k, v in obj.items() if k not in ignore_keys}
    if isinstance(obj, list):
        return [_strip_ignored(v, ignore_keys) for v in obj]
    return obj


def save_json_if_changed(path: str, data: object, ignore_keys: tuple[str, ...] = ("maj",)) -> bool:
    """Écrit un JSON compact seulement si le contenu utile a changé (hors clés ignorées)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                old = json.load(f)
            if _strip_ignored(old, ignore_keys) == _strip_ignored(data, ignore_keys):
                return False
        except (OSError, json.JSONDecodeError):
            pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    return True
