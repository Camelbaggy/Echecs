#!/usr/bin/env python3
"""Collecteur du classement mensuel officiel FIDE (ratings.fide.com).

Télécharge les listes XML officielles (Classique, Rapide, Blitz) et produit
fide/classement.json (Top 100 Monde, Top 50 France, Top 50 Femmes, Top 30 Juniors).
Python 3.11 stdlib uniquement.
"""

from __future__ import annotations

import datetime as dt
import io
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

import netutil

FIDE_LISTS_URL = "https://ratings.fide.com/download_lists.phtml"

_TITRES_FIDE = {
    "g": "GM", "m": "IM", "f": "FM", "c": "CM",
    "wg": "WGM", "wm": "WIM", "wf": "WFM", "wc": "WCM",
    "GM": "GM", "IM": "IM", "FM": "FM", "CM": "CM",
    "WGM": "WGM", "WIM": "WIM", "WFM": "WFM", "WCM": "WCM",
}


def _norm_titre(t: str) -> str:
    return _TITRES_FIDE.get((t or "").strip(), (t or "").strip().upper())


def decouvrir_urls_fide(html: str) -> dict[str, str]:
    """Extrait les URLs des fichiers XML officiels (standard, rapid, blitz) depuis la page FIDE."""
    liens = re.findall(r'href=["\']([^"\']+\.zip)["\']', html, flags=re.IGNORECASE)
    trouves: dict[str, str] = {}
    for href in liens:
        url = href if href.startswith("http") else ("https://ratings.fide.com/" + href.lstrip("/"))
        low = url.lower()
        if "standard_rating_list_xml" in low and "standard" not in trouves:
            trouves["standard"] = url
        elif "rapid_rating_list_xml" in low and "rapid" not in trouves:
            trouves["rapid"] = url
        elif "blitz_rating_list_xml" in low and "blitz" not in trouves:
            trouves["blitz"] = url
    return trouves


def extraire_periode_fide(html: str) -> str:
    """Extrait la période (ex. 'November 2026' -> '2026-11') depuis la page FIDE."""
    mois_en = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12",
    }
    m = re.search(r"Download\s+([A-Za-z]+)\s+(\d{4})\s+FRL", html)
    if m:
        mo = mois_en.get(m.group(1).lower())
        if mo:
            return f"{m.group(2)}-{mo}"
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")


def parser_liste_xml(xml_bytes: bytes, annee_ref: int | None = None) -> dict:
    """Parse une liste XML FIDE en streaming et extrait Monde / France / Femmes / Juniors."""
    if annee_ref is None:
        annee_ref = dt.datetime.now(dt.timezone.utc).year

    monde: list[dict] = []
    femmes: list[dict] = []
    france: list[dict] = []
    juniors: list[dict] = []
    total = 0
    actifs = 0

    for _, elem in ET.iterparse(io.BytesIO(xml_bytes), events=("end",)):
        if elem.tag != "player":
            continue
        total += 1
        flag = (elem.findtext("flag") or "").strip().lower()
        rtg_s = (elem.findtext("rating") or "").strip()
        if "i" not in flag and rtg_s.isdigit():
            actifs += 1
            elo = int(rtg_s)
            fed = (elem.findtext("country") or "").strip().upper()
            sex = (elem.findtext("sex") or "").strip().upper()
            bday_s = (elem.findtext("birthday") or "").strip()
            bday = int(bday_s) if bday_s.isdigit() else None
            is_junior = bday is not None and (annee_ref - bday) <= 20

            # Filtrer tôt pour garder une empreinte mémoire minuscule
            keep_monde = elo >= 2580
            keep_femmes = sex == "F" and elo >= 2300
            keep_france = fed == "FRA" and elo >= 2300
            keep_juniors = is_junior and elo >= 2450

            if keep_monde or keep_femmes or keep_france or keep_juniors:
                rec = {
                    "id": (elem.findtext("fideid") or "").strip(),
                    "nom": (elem.findtext("name") or "").strip(),
                    "fed": fed,
                    "drapeau": netutil.fide_flag(fed),
                    "francais": fed == "FRA",
                    "titre": _norm_titre(elem.findtext("title") or ""),
                    "elo": elo,
                    "parties": int(elem.findtext("games") or 0) if (elem.findtext("games") or "").isdigit() else 0,
                    "naissance": bday,
                    "sexe": sex,
                }
                if keep_monde:
                    monde.append(rec)
                if keep_femmes:
                    femmes.append(rec)
                if keep_france:
                    france.append(rec)
                if keep_juniors:
                    juniors.append(rec)
        elem.clear()

    def _top(lst: list[dict], limit: int) -> list[dict]:
        lst.sort(key=lambda x: (-x["elo"], -x["parties"], x["nom"]))
        out = []
        for idx, item in enumerate(lst[:limit], 1):
            row = dict(item)
            row["rang"] = idx
            out.append(row)
        return out

    return {
        "total_joueurs": total,
        "actifs": actifs,
        "monde": _top(monde, 100),
        "femmes": _top(femmes, 50),
        "france": _top(france, 50),
        "juniors": _top(juniors, 30),
    }


def collecter_fide() -> dict:
    """Télécharge et agrège les classements FIDE Standard, Rapide et Blitz du mois."""
    html = netutil.fetch_text(FIDE_LISTS_URL, timeout=30)
    urls = decouvrir_urls_fide(html)
    periode = extraire_periode_fide(html)
    annee = int(periode[:4]) if periode[:4].isdigit() else dt.datetime.now(dt.timezone.utc).year

    if not urls:
        raise RuntimeError("Aucun lien ZIP XML FIDE trouvé sur download_lists.phtml")

    result: dict = {
        "periode": periode,
        "source": "FIDE",
        "maj": netutil.utc_now_iso(),
        "monde": {},
        "femmes": {},
        "france": {},
        "juniors": {},
        "stats": {},
    }

    for cadence in ("standard", "rapid", "blitz"):
        url = urls.get(cadence)
        if not url:
            continue
        print(f"[FIDE] Téléchargement {cadence} : {url}")
        raw_zip = netutil.fetch_bytes(url, timeout=90, min_interval=1.0)
        with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
            xml_name = next((n for n in zf.namelist() if n.lower().endswith(".xml")), zf.namelist()[0])
            xml_bytes = zf.read(xml_name)
        parsed = parser_liste_xml(xml_bytes, annee_ref=annee)
        limit_m = 100 if cadence == "standard" else 50
        limit_f = 50 if cadence == "standard" else 30
        result["monde"][cadence] = parsed["monde"][:limit_m]
        result["femmes"][cadence] = parsed["femmes"][:limit_f]
        result["france"][cadence] = parsed["france"][:limit_f]
        if cadence == "standard":
            result["juniors"]["standard"] = parsed["juniors"][:30]
        result["stats"][cadence] = {
            "total": parsed["total_joueurs"],
            "actifs": parsed["actifs"],
        }
        print(f"[FIDE] {cadence} : {parsed['actifs']} actifs / {parsed['total_joueurs']} joueurs")

    return result


def _run_tests() -> None:
    html = """
    <h2>Download November 2026 FRL</h2>
    <a href="http://ratings.fide.com/download/standard_rating_list_xml.zip">XML</a>
    <a href="/download/rapid_rating_list_xml.zip">XML</a>
    <a href="/download/blitz_rating_list_xml.zip">XML</a>
    """
    urls = decouvrir_urls_fide(html)
    assert set(urls.keys()) == {"standard", "rapid", "blitz"}
    assert extraire_periode_fide(html) == "2026-11"

    xml_sample = b"""<?xml version="1.0" encoding="UTF-8"?>
    <playerslist>
      <player><fideid>1503014</fideid><name>Carlsen, Magnus</name><country>NOR</country><sex>M</sex><title>GM</title><rating>2832</rating><games>12</games><birthday>1990</birthday><flag></flag></player>
      <player><fideid>12573981</fideid><name>Firouzja, Alireza</name><country>FRA</country><sex>M</sex><title>GM</title><rating>2763</rating><games>15</games><birthday>2003</birthday><flag></flag></player>
      <player><fideid>36085755</fideid><name>Maurizzi, Marc'Andria</name><country>FRA</country><sex>M</sex><title>GM</title><rating>2625</rating><games>18</games><birthday>2007</birthday><flag></flag></player>
      <player><fideid>8602980</fideid><name>Hou, Yifan</name><country>CHN</country><sex>F</sex><title>GM</title><rating>2633</rating><games>4</games><birthday>1994</birthday><flag></flag></player>
      <player><fideid>4100018</fideid><name>Kasparov, Garry</name><country>RUS</country><sex>M</sex><title>GM</title><rating>2812</rating><games>0</games><birthday>1963</birthday><flag>i</flag></player>
    </playerslist>
    """
    parsed = parser_liste_xml(xml_sample, annee_ref=2026)
    assert parsed["total_joueurs"] == 5
    assert parsed["actifs"] == 4  # Kasparov inactif exclu
    assert parsed["monde"][0]["nom"] == "Carlsen, Magnus"
    assert len(parsed["france"]) == 2 and parsed["france"][0]["nom"] == "Firouzja, Alireza"
    assert len(parsed["femmes"]) == 1 and parsed["femmes"][0]["nom"] == "Hou, Yifan"
    assert len(parsed["juniors"]) == 1 and parsed["juniors"][0]["nom"] == "Maurizzi, Marc'Andria"

    print("[OK] fide.py : tous les tests passent.")


if __name__ == "__main__":
    if "--test" in sys.argv:
        _run_tests()
    else:
        print("Usage: python3 fide.py --test")
