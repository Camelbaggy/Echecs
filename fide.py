#!/usr/bin/env python3
"""Collecteur du classement mensuel officiel FIDE.

1. Source directe : listes XML officielles sur ratings.fide.com (Standard, Rapide, Blitz).
2. Repli automatique (lorsque ratings.fide.com bloque les IP GitHub Actions) :
   - Découverte dynamique des identifiants FIDE (P1440) et du genre féminin (P21=Q6581072)
     via Wikidata SPARQL (Top mondial, GMI/MI/GMF français, GMI/GMF féminines) + joueurs
     des tournois collectés. Aucune liste d'identifiants codée en dur de mémoire.
   - Lecture des classements officiels mensuels (Standard, Rapide, Blitz) via l'API
     officielle Lichess FIDE : GET /api/fide/player/{fideId}.
Produit fide/classement.json (Monde, France, Femmes, Juniors). Python 3.11 stdlib uniquement.
"""

from __future__ import annotations

import datetime as dt
import glob
import io
import json
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile

import netutil

FIDE_LISTS_URL = "https://ratings.fide.com/download_lists.phtml"
LICHESS_FIDE_API = "https://lichess.org/api/fide/player"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"

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


def _sparql_fide_ids(query: str) -> list[str]:
    url = WIKIDATA_SPARQL + "?format=json&query=" + urllib.parse.quote(query)
    data = netutil.fetch_json(
        url,
        headers={"Accept": "application/sparql-results+json"},
        min_interval=1.5,
        timeout=35,
    )
    rows = (data.get("results") or {}).get("bindings", []) if isinstance(data, dict) else []
    ids: list[str] = []
    for r in rows:
        fid = (r.get("fide") or {}).get("value", "").strip()
        if fid.isdigit():
            ids.append(fid)
    return list(dict.fromkeys(ids))


def decouvrir_ids_wikidata() -> tuple[list[str], set[str]]:
    """Découvre via Wikidata (P1440 + P21) les FIDE IDs vérifiés : Top monde, France, Femmes.

    Retourne (tous_les_ids, ids_femmes_verifies).
    """
    # 1. Top mondial (joueurs avec P1440 et Elo Wikidata >= 2650, nés >= 1965)
    q_monde = """
    SELECT DISTINCT ?fide WHERE {
      ?item wdt:P1440 ?fide ;
            wdt:P1087 ?elo ;
            wdt:P569 ?dob .
      FILTER(?elo >= 2650 && YEAR(?dob) >= 1965)
      FILTER NOT EXISTS { ?item wdt:P570 ?dod . }
    }
    ORDER BY DESC(?elo)
    LIMIT 140
    """
    # 2. Joueurs français titrés (GM Q105269, WGM Q752119, IM Q491675) liés à la France (Q142)
    q_france = """
    SELECT DISTINCT ?fide WHERE {
      ?item wdt:P1440 ?fide ;
            wdt:P2962 ?tit .
      VALUES ?tit { wd:Q105269 wd:Q752119 wd:Q491675 }
      { ?item wdt:P1532 wd:Q142 . } UNION { ?item wdt:P27 wd:Q142 . }
      OPTIONAL { ?item wdt:P1087 ?elo . }
      FILTER NOT EXISTS { ?item wdt:P570 ?dod . }
    }
    ORDER BY DESC(?elo)
    LIMIT 120
    """
    # 3. Joueuses féminines vérifiées (P21 = Q6581072, GM ou WGM ou Elo >= 2350)
    q_femmes = """
    SELECT DISTINCT ?fide WHERE {
      ?item wdt:P1440 ?fide ;
            wdt:P21 wd:Q6581072 ;
            wdt:P1087 ?elo .
      FILTER(?elo >= 2380)
      FILTER NOT EXISTS { ?item wdt:P570 ?dod . }
    }
    ORDER BY DESC(?elo)
    LIMIT 90
    """
    ids_monde: list[str] = []
    ids_france: list[str] = []
    ids_femmes: list[str] = []
    for label, q, dest in (
        ("monde", q_monde, ids_monde),
        ("france", q_france, ids_france),
        ("femmes", q_femmes, ids_femmes),
    ):
        try:
            found = _sparql_fide_ids(q)
            dest.extend(found)
            print(f"[FIDE/Wikidata] {label} : {len(found)} FIDE IDs vérifiés")
        except Exception as e:
            print(f"[WARN] Wikidata {label} : {e}")

    tous = list(dict.fromkeys(ids_monde + ids_france + ids_femmes))
    return tous, set(ids_femmes)


def _ids_depuis_tournois(dossier_tournois: str = "tournois") -> list[str]:
    """Ajoute les FIDE IDs des joueurs (>= 2550 ou FRA >= 2250) présents dans les tournois collectés."""
    extra: list[str] = []
    for path in sorted(glob.glob(os.path.join(dossier_tournois, "*.json"))):
        if os.path.basename(path) == "index.json":
            continue
        try:
            with open(path, "r", encoding="utf-8") as fp:
                t = json.load(fp)
        except Exception:
            continue
        for j in t.get("classement", []):
            fid = str(j.get("fide_id") or "").strip()
            elo = j.get("elo") or 0
            if not fid or not fid.isdigit():
                continue
            if elo >= 2550 or (j.get("francais") and elo >= 2250):
                extra.append(fid)
    return extra


def normaliser_joueur_lichess_fide(raw: dict, *, ids_femmes: set[str] | None = None) -> dict | None:
    """Normalise une réponse de GET /api/fide/player/{id}."""
    fid = str(raw.get("id") or "").strip()
    nom = (raw.get("name") or "").strip()
    if not fid or not nom:
        return None
    if raw.get("inactive"):
        return None
    fed = (raw.get("federation") or "").strip().upper()
    titre = _norm_titre(raw.get("title") or "")
    est_femme = titre.startswith("W") or (ids_femmes is not None and fid in ids_femmes)
    return {
        "id": fid,
        "nom": nom,
        "fed": fed,
        "drapeau": netutil.fide_flag(fed),
        "francais": fed == "FRA",
        "titre": titre,
        "standard": raw.get("standard"),
        "rapid": raw.get("rapid"),
        "blitz": raw.get("blitz"),
        "naissance": raw.get("year"),
        "sexe": "F" if est_femme else "",
    }


def collecter_fide_api_lichess(dossier_tournois: str = "tournois") -> dict:
    """Interroge Wikidata (IDs + genre vérifiés) puis l'API officielle Lichess FIDE."""
    periode = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
    annee = int(periode[:4])

    ids_wd, ids_femmes = decouvrir_ids_wikidata()
    ids = list(dict.fromkeys(ids_wd + _ids_depuis_tournois(dossier_tournois)))
    print(f"[FIDE/Lichess API] Interrogation de {len(ids)} joueurs vérifiés via /api/fide/player/{{id}}…")

    joueurs: list[dict] = []
    for fid in ids:
        try:
            raw = netutil.fetch_json(f"{LICHESS_FIDE_API}/{fid}", min_interval=0.22, timeout=15, retries=2)
            if isinstance(raw, dict):
                rec = normaliser_joueur_lichess_fide(raw, ids_femmes=ids_femmes)
                if rec:
                    joueurs.append(rec)
        except Exception as e:
            print(f"[WARN] FIDE ID {fid} : {e}")

    def _classer(subset: list[dict], cad: str, limit: int, min_elo: int = 0) -> list[dict]:
        cands = [j for j in subset if isinstance(j.get(cad), int) and j[cad] >= min_elo]
        cands.sort(key=lambda x: (-x[cad], x["nom"]))
        out = []
        for idx, j in enumerate(cands[:limit], 1):
            out.append({
                "rang": idx,
                "id": j["id"],
                "nom": j["nom"],
                "fed": j["fed"],
                "drapeau": j["drapeau"],
                "francais": j["francais"],
                "titre": j["titre"],
                "elo": j[cad],
                "parties": 0,
                "naissance": j["naissance"],
                "sexe": j["sexe"],
            })
        return out

    fra = [j for j in joueurs if j["francais"]]
    fem = [j for j in joueurs if j["sexe"] == "F"]
    jun = [j for j in joueurs if j.get("naissance") and (annee - j["naissance"]) <= 20]

    res = {
        "periode": periode,
        "source": "FIDE (API Lichess + Wikidata)",
        "maj": netutil.utc_now_iso(),
        "monde": {c: _classer(joueurs, c, 100 if c == "standard" else 50, min_elo=2500) for c in ("standard", "rapid", "blitz")},
        "france": {c: _classer(fra, c, 50 if c == "standard" else 30, min_elo=2200) for c in ("standard", "rapid", "blitz")},
        "femmes": {c: _classer(fem, c, 50 if c == "standard" else 30, min_elo=2200) for c in ("standard", "rapid", "blitz")},
        "juniors": {"standard": _classer(jun, "standard", 30, min_elo=2400)},
        "stats": {"joueurs_interroges": len(joueurs)},
    }
    print(
        f"[FIDE/Lichess API] {len(joueurs)} joueurs lus : "
        f"Monde={len(res['monde']['standard'])}, France={len(res['france']['standard'])}, "
        f"Femmes={len(res['femmes']['standard'])}, Juniors={len(res['juniors']['standard'])}"
    )
    return res


def collecter_fide(dossier_tournois: str = "tournois") -> dict:
    """Télécharge les classements FIDE (ratings.fide.com direct, sinon Wikidata + API Lichess FIDE)."""
    try:
        html_txt = netutil.fetch_text(FIDE_LISTS_URL, timeout=10, retries=1)
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
        print(f"[WARN] ratings.fide.com injoignable ({e}) -> bascule sur Wikidata + API Lichess FIDE")
        return collecter_fide_api_lichess(dossier_tournois)


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

    api_rec = normaliser_joueur_lichess_fide(
        {"id": 8602980, "name": "Hou, Yifan", "title": "GM", "federation": "CHN", "year": 1994, "standard": 2596},
        ids_femmes={"8602980"},
    )
    assert api_rec is not None and api_rec["sexe"] == "F"
    api_man = normaliser_joueur_lichess_fide(
        {"id": 4147235, "name": "Tomashevsky, Evgeny", "title": "GM", "federation": "RUS", "year": 1987, "standard": 2681},
        ids_femmes={"8602980"},
    )
    assert api_man is not None and api_man["sexe"] == ""

    print("[OK] fide.py : tous les tests passent.")


if __name__ == "__main__":
    if "--test" in sys.argv:
        _run_tests()
    else:
        print("Usage: python3 fide.py --test")
