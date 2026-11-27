#!/usr/bin/env python3
"""Collecteur du classement mensuel officiel FIDE.

1. Source directe : listes XML officielles sur ratings.fide.com (Standard, Rapide, Blitz).
2. Repli automatique : miroir officiel FIDE hébergé par Lichess (https://lichess.org/fide
   et https://lichess.org/fide/federation/France), lorsque ratings.fide.com est injoignable
   depuis GitHub Actions.
Produit fide/classement.json (Monde, France, Femmes, Juniors). Python 3.11 stdlib uniquement.
"""

from __future__ import annotations

import datetime as dt
import html as html_lib
import io
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

import netutil

FIDE_LISTS_URL = "https://ratings.fide.com/download_lists.phtml"
LICHESS_FIDE_BASE = "https://lichess.org/fide"

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


def parser_page_lichess_fide(page_html: str, *, cadence: str = "standard", fed_defaut: str = "", annee_ref: int | None = None) -> list[dict]:
    """Extrait les joueurs d'une page HTML lichess.org/fide (miroir officiel des listes FIDE)."""
    if annee_ref is None:
        annee_ref = dt.datetime.now(dt.timezone.utc).year
    rows: list[dict] = []
    # Chaque ligne joueur dans <tbody> contient un lien /fide/<id>/... et les colonnes Classical, Rapid, Blitz, Age
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page_html, flags=re.DOTALL | re.IGNORECASE):
        m_id = re.search(r'href="/fide/(\d+)/[^"]*"', tr)
        if not m_id:
            continue
        fid = m_id.group(1)

        # Fédération : souvent dans .../fed-webp/FRA.webp ou fed/FRA
        m_fed = re.search(r"/fed[^/]*/([A-Z]{3})\.(?:webp|png|svg)", tr)
        fed = m_fed.group(1) if m_fed else fed_defaut

        # Titre éventuel (ex. <span class="utitle">GM</span>)
        m_tit = re.search(r'<span[^>]*class="[^"]*utitle[^"]*"[^>]*>([^<]+)</span>', tr)
        titre = _norm_titre(m_tit.group(1)) if m_tit else ""

        # Extraire toutes les cellules <td>
        tds = re.findall(r"<td\b[^>]*>(.*?)</td>", tr, flags=re.DOTALL | re.IGNORECASE)
        if len(tds) < 4:
            continue

        # Nom dans la 1re cellule (retirer balises HTML, titre et nom de fédération éventuel)
        # Lichess met le nom dans <a ...>...</a> ou <span class="name">
        m_name = re.search(r'<a[^>]*href="/fide/\d+/[^"]*"[^>]*>(.*?)</a>', tds[0], flags=re.DOTALL)
        raw_name = m_name.group(1) if m_name else tds[0]
        raw_name = re.sub(r'<span[^>]*class="[^"]*utitle[^"]*"[^>]*>.*?</span>', "", raw_name, flags=re.DOTALL)
        raw_name = re.sub(r"<[^>]+>", " ", raw_name)
        nom = re.sub(r"\s+", " ", html_lib.unescape(raw_name)).strip()

        def _int_cell(cell_html: str) -> int | None:
            txt = re.sub(r"<[^>]+>", "", cell_html).strip()
            return int(txt) if txt.isdigit() else None

        std = _int_cell(tds[1]) if len(tds) > 1 else None
        rpd = _int_cell(tds[2]) if len(tds) > 2 else None
        blz = _int_cell(tds[3]) if len(tds) > 3 else None
        age = _int_cell(tds[4]) if len(tds) > 4 else None

        elo_map = {"standard": std, "rapid": rpd, "blitz": blz}
        elo = elo_map.get(cadence)
        if elo is None:
            continue

        rows.append({
            "id": fid,
            "nom": nom,
            "fed": fed,
            "drapeau": netutil.fide_flag(fed),
            "francais": fed == "FRA",
            "titre": titre,
            "elo": elo,
            "parties": 0,
            "naissance": (annee_ref - age) if age is not None else None,
            "sexe": "F" if titre.startswith("W") else "",
        })
    return rows


def collecter_fide_lichess() -> dict:
    """Collecte les classements FIDE mensuels via le miroir officiel lichess.org/fide."""
    periode = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
    annee = int(periode[:4])

    def _charger(base_url: str, cadence: str, pages: int, limit: int, fed_defaut: str = "") -> list[dict]:
        seen: set[str] = set()
        out: list[dict] = []
        for p in range(1, pages + 1):
            sep = "&" if "?" in base_url else "?"
            url = f"{base_url}{sep}page={p}&order={cadence}"
            html_txt = netutil.fetch_text(url, min_interval=0.8, timeout=25)
            parsed = parser_page_lichess_fide(html_txt, cadence=cadence, fed_defaut=fed_defaut, annee_ref=annee)
            if not parsed:
                break
            for row in parsed:
                if row["id"] in seen:
                    continue
                seen.add(row["id"])
                out.append(row)
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break
        for idx, row in enumerate(out, 1):
            row["rang"] = idx
        return out

    print("[FIDE] Repli sur le miroir officiel Lichess (lichess.org/fide)…")
    monde_std = _charger(LICHESS_FIDE_BASE, "standard", pages=4, limit=100)
    monde_rpd = _charger(LICHESS_FIDE_BASE, "rapid", pages=2, limit=50)
    monde_blz = _charger(LICHESS_FIDE_BASE, "blitz", pages=2, limit=50)

    fra_base = f"{LICHESS_FIDE_BASE}/federation/France"
    fra_std = _charger(fra_base, "standard", pages=2, limit=50, fed_defaut="FRA")
    fra_rpd = _charger(fra_base, "rapid", pages=1, limit=30, fed_defaut="FRA")
    fra_blz = _charger(fra_base, "blitz", pages=1, limit=30, fed_defaut="FRA")

    juniors = [dict(r) for r in monde_std if r.get("naissance") and (annee - r["naissance"]) <= 20]
    for idx, r in enumerate(juniors, 1):
        r["rang"] = idx

    print(f"[FIDE/Lichess] Monde STD={len(monde_std)}, France STD={len(fra_std)}, Juniors={len(juniors)}")
    return {
        "periode": periode,
        "source": "FIDE (via Lichess)",
        "maj": netutil.utc_now_iso(),
        "monde": {"standard": monde_std, "rapid": monde_rpd, "blitz": monde_blz},
        "femmes": {"standard": [], "rapid": [], "blitz": []},
        "france": {"standard": fra_std, "rapid": fra_rpd, "blitz": fra_blz},
        "juniors": {"standard": juniors},
        "stats": {},
    }


def collecter_fide() -> dict:
    """Télécharge les classements FIDE (ratings.fide.com direct, sinon miroir lichess.org/fide)."""
    try:
        html_txt = netutil.fetch_text(FIDE_LISTS_URL, timeout=12, retries=1)
        urls = decouvrir_urls_fide(html_txt)
        if not urls:
            raise RuntimeError("Aucun lien ZIP XML FIDE trouvé")
        periode = extraire_periode_fide(html_txt)
        annee = int(periode[:4]) if periode[:4].isdigit() else dt.datetime.now(dt.timezone.utc).year

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
            raw_zip = netutil.fetch_bytes(url, timeout=60, retries=2, min_interval=1.0)
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
            result["stats"][cadence] = {"total": parsed["total_joueurs"], "actifs": parsed["actifs"]}
        return result
    except Exception as e:
        print(f"[WARN] ratings.fide.com injoignable ({e}) -> bascule sur lichess.org/fide")
        return collecter_fide_lichess()


def _run_tests() -> None:
    html_src = """
    <h2>Download November 2026 FRL</h2>
    <a href="http://ratings.fide.com/download/standard_rating_list_xml.zip">XML</a>
    <a href="/download/rapid_rating_list_xml.zip">XML</a>
    <a href="/download/blitz_rating_list_xml.zip">XML</a>
    """
    urls = decouvrir_urls_fide(html_src)
    assert set(urls.keys()) == {"standard", "rapid", "blitz"}
    assert extraire_periode_fide(html_src) == "2026-11"

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
    assert parsed["total_joueurs"] == 5 and parsed["actifs"] == 4
    assert parsed["monde"][0]["nom"] == "Carlsen, Magnus"
    assert len(parsed["france"]) == 2 and parsed["france"][0]["nom"] == "Firouzja, Alireza"

    lichess_html = """
    <table><tbody>
      <tr>
        <td><a href="/fide/12573981/Firouzja_Alireza"><span class="utitle">GM</span> Firouzja, Alireza</a><img src="/assets/fide/fed-webp/FRA.webp"></td>
        <td>2757</td><td>2737</td><td>2797</td><td>23</td>
      </tr>
    </tbody></table>
    """
    lp = parser_page_lichess_fide(lichess_html, cadence="standard", annee_ref=2026)
    assert len(lp) == 1 and lp[0]["id"] == "12573981" and lp[0]["fed"] == "FRA" and lp[0]["elo"] == 2757 and lp[0]["naissance"] == 2003

    print("[OK] fide.py : tous les tests passent.")


if __name__ == "__main__":
    if "--test" in sys.argv:
        _run_tests()
    else:
        print("Usage: python3 fide.py --test")
