#!/usr/bin/env python3
"""Fiches joueurs (joueurs/<fide_id>.json + joueurs/index.json).

Agrège les parties des tournois collectés, les classements FIDE (Standard/Rapide/Blitz),
Wikidata (P1440 = identifiant FIDE officiel) et Wikipédia FR (lots MediaWiki de 20 titres,
cache 30 jours, CC BY-SA 4.0). Jamais bloquant en cas d'erreur réseau.
"""

from __future__ import annotations

import datetime as dt
import glob
import json
import os
import sys
import time
import urllib.parse

import netutil

CACHE_JOURS = 30


def _cache_valide(maj: str | None) -> bool:
    if not maj:
        return False
    try:
        t = dt.datetime.strptime(maj[:10], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
        return (dt.datetime.now(dt.timezone.utc) - t).days < CACHE_JOURS
    except ValueError:
        return False


def _couper_extrait(texte: str, cible: int = 260, maxi: int = 850) -> str:
    paras = [p.strip() for p in (texte or "").split("\n") if p.strip()]
    out: list[str] = []
    total = 0
    for p in paras:
        if total + len(p) > maxi and out:
            break
        out.append(p)
        total += len(p)
        if total >= cible:
            break
    res = "\n\n".join(out)
    if len(res) > maxi:
        res = res[: maxi - 1].rsplit(" ", 1)[0] + "…"
    return res


def interroger_wikidata(fide_ids: list[str]) -> dict[str, dict]:
    """Interroge Wikidata par lots via P1440 (identifiant FIDE)."""
    result: dict[str, dict] = {}
    for i in range(0, len(fide_ids), 80):
        lot = [fid for fid in fide_ids[i : i + 80] if fid and fid.isdigit()]
        if not lot:
            continue
        values = " ".join(f'"{fid}"' for fid in lot)
        query = f"""
        SELECT ?fide ?item ?dob ?lieuLabel ?image ?wpfr WHERE {{
          VALUES ?fide {{ {values} }}
          ?item wdt:P1440 ?fide .
          OPTIONAL {{ ?item wdt:P569 ?dob . }}
          OPTIONAL {{ ?item wdt:P19 ?lieu . }}
          OPTIONAL {{ ?item wdt:P18 ?image . }}
          OPTIONAL {{
            ?wpfr schema:about ?item ;
                  schema:isPartOf <https://fr.wikipedia.org/> .
          }}
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "fr,en". }}
        }}
        """
        url = "https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(query)
        try:
            data = netutil.fetch_json(
                url,
                headers={"Accept": "application/sparql-results+json"},
                min_interval=1.5,
                timeout=30,
            )
        except Exception as e:
            print(f"[WARN] Wikidata lot {i} : {e}")
            break
        rows = (data.get("results") or {}).get("bindings", []) if isinstance(data, dict) else []
        for r in rows:
            fid = (r.get("fide") or {}).get("value", "")
            if not fid or fid in result:
                continue
            qid = (r.get("item") or {}).get("value", "").rsplit("/", 1)[-1]
            dob = (r.get("dob") or {}).get("value", "")[:10]
            wp_url = (r.get("wpfr") or {}).get("value", "")
            titre_wp = urllib.parse.unquote(wp_url.rsplit("/wiki/", 1)[-1]) if "/wiki/" in wp_url else ""
            img_url = (r.get("image") or {}).get("value", "")
            result[fid] = {
                "qid": qid,
                "naissance": dob if len(dob) == 10 else "",
                "lieu_naissance": (r.get("lieuLabel") or {}).get("value", ""),
                "photo_commons": img_url,
                "titre_wikipedia": titre_wp,
                "maj": netutil.utc_now_iso(),
            }
    return result


def interroger_wikipedia_fr(titres: list[str]) -> dict[str, dict]:
    """Lit les extraits Wikipédia FR par lots de 20 titres (API MediaWiki)."""
    result: dict[str, dict] = {}
    uniques = list(dict.fromkeys(t for t in titres if t))
    echecs = 0
    for i in range(0, len(uniques), 20):
        lot = uniques[i : i + 20]
        url = (
            "https://fr.wikipedia.org/w/api.php?action=query&format=json"
            "&prop=extracts|info|pageprops&exintro=1&explaintext=1&redirects=1&inprop=url"
            "&titles=" + urllib.parse.quote("|".join(lot))
        )
        try:
            data = netutil.fetch_json(url, min_interval=2.2, timeout=25)
            echecs = 0
        except Exception as e:
            print(f"[WARN] Wikipédia FR lot {i} : {e}")
            echecs += 1
            if echecs >= 3:
                break
            continue

        query = data.get("query") or {} if isinstance(data, dict) else {}
        redirects = {r["to"]: r["from"] for r in (query.get("redirects") or [])}
        norm = {n["to"]: n["from"] for n in (query.get("normalized") or [])}
        for page in (query.get("pages") or {}).values():
            if "missing" in page:
                continue
            if "disambiguation" in (page.get("pageprops") or {}):
                continue
            ext = _couper_extrait(page.get("extract") or "")
            if not ext:
                continue
            title = page.get("title", "")
            rec = {
                "titre": title,
                "extrait": ext,
                "url": page.get("fullurl", ""),
                "licence": "CC BY-SA 4.0",
                "maj": netutil.utc_now_iso(),
            }
            result[title] = rec
            if title in redirects:
                result[redirects[title]] = rec
            if title in norm:
                result[norm[title]] = rec
            for k in lot:
                if k.replace("_", " ") == title:
                    result[k] = rec
    return result


def construire_fiches(
    dossier_tournois: str,
    fichier_fide: str,
    dossier_sortie: str,
    *,
    avec_wiki: bool = True,
    max_wiki: int = 80,
) -> dict:
    """Construit toutes les fiches joueurs et joueurs/index.json."""
    fiches: dict[str, dict] = {}

    def _get(fid: str, nom: str, fed: str = "", titre: str = "") -> dict:
        if fid not in fiches:
            fiches[fid] = {
                "id": fid,
                "nom": nom,
                "fed": fed,
                "drapeau": netutil.fide_flag(fed),
                "pays": netutil.fide_pays(fed),
                "francais": fed == "FRA",
                "titre": titre,
                "elo": {},
                "rangs": {},
                "bilan": {
                    "parties": 0, "v": 0, "n": 0, "d": 0,
                    "blancs": {"v": 0, "n": 0, "d": 0},
                    "noirs": {"v": 0, "n": 0, "d": 0},
                },
                "_precisions": [],
                "tournois": [],
                "parties": [],
                "maj": netutil.utc_now_iso(),
            }
        f = fiches[fid]
        if nom and (not f["nom"] or f["nom"] == "?"):
            f["nom"] = nom
        if fed and not f["fed"]:
            f["fed"] = fed
            f["drapeau"] = netutil.fide_flag(fed)
            f["pays"] = netutil.fide_pays(fed)
            f["francais"] = fed == "FRA"
        if titre and not f["titre"]:
            f["titre"] = titre
        return f

    # 1. Charger les classements FIDE (Top Monde + Top France)
    if os.path.exists(fichier_fide):
        with open(fichier_fide, "r", encoding="utf-8") as fp:
            fide_data = json.load(fp)
        for cat, cle_rang in (("monde", "monde"), ("france", "france"), ("femmes", "femmes")):
            for cad in ("standard", "rapid", "blitz"):
                for row in (fide_data.get(cat) or {}).get(cad) or []:
                    fid = row.get("id", "")
                    if not fid:
                        continue
                    f = _get(fid, row["nom"], row.get("fed", ""), row.get("titre", ""))
                    f["elo"][cad] = row["elo"]
                    f["rangs"][f"{cle_rang}_{cad}"] = row["rang"]
                    if row.get("naissance") and not f.get("annee_naissance"):
                        f["annee_naissance"] = row["naissance"]

    # 2. Agréger les tournois collectés
    for path in sorted(glob.glob(os.path.join(dossier_tournois, "*.json"))):
        if os.path.basename(path) == "index.json":
            continue
        with open(path, "r", encoding="utf-8") as fp:
            t = json.load(fp)
        tid = t["id"]
        tnom = t["nom"]
        for j in t.get("classement", []):
            fid = j.get("fide_id", "")
            if not fid:
                continue
            f = _get(fid, j["nom"], j.get("fed", ""), j.get("titre", ""))
            if j.get("elo") and "standard" not in f["elo"]:
                f["elo"]["tournoi"] = j["elo"]
            f["tournois"].append({
                "id": tid,
                "nom": tnom,
                "debut": t.get("debut", ""),
                "rang": j.get("rang"),
                "score": j.get("score"),
                "jouees": j.get("jouees"),
                "perf": j.get("perf"),
            })

        for r in t.get("rondes", []):
            for p in r.get("parties", []):
                if p.get("statut") != "terminee":
                    continue
                res = p.get("resultat")
                ana = p.get("analyse") or {}
                for camp, adv_camp in (("blancs", "noirs"), ("noirs", "blancs")):
                    me = p[camp]
                    adv = p[adv_camp]
                    fid = me.get("fide_id", "")
                    if not fid:
                        continue
                    f = _get(fid, me["nom"], me.get("fed", ""), me.get("titre", ""))
                    if (camp == "blancs" and res == "1-0") or (camp == "noirs" and res == "0-1"):
                        issue = "v"
                    elif res == "1/2-1/2":
                        issue = "n"
                    else:
                        issue = "d"
                    f["bilan"]["parties"] += 1
                    f["bilan"][issue] += 1
                    f["bilan"][camp][issue] += 1
                    prec = (ana.get(camp) or {}).get("precision")
                    if prec is not None:
                        f["_precisions"].append(prec)
                    f["parties"].append({
                        "tournoi_id": tid,
                        "tournoi": tnom,
                        "ronde_id": r["id"],
                        "ronde": r["nom"],
                        "partie_id": p["id"],
                        "date": p.get("date", ""),
                        "couleur": camp,
                        "adversaire": adv["nom"],
                        "adversaire_elo": adv.get("elo"),
                        "adversaire_drapeau": adv.get("drapeau", ""),
                        "resultat": res,
                        "issue": issue,
                        "precision": prec,
                        "eco": p.get("eco", ""),
                    })

    # 3. Recharger le cache Wikidata/Wikipédia existant et rafraîchir si besoin
    a_rafraichir_wd: list[str] = []
    for fid, f in fiches.items():
        old_path = os.path.join(dossier_sortie, f"{fid}.json")
        if os.path.exists(old_path):
            try:
                with open(old_path, "r", encoding="utf-8") as fp:
                    old = json.load(fp)
                if old.get("wikidata"):
                    f["wikidata"] = old["wikidata"]
                if old.get("wikipedia"):
                    f["wikipedia"] = old["wikipedia"]
            except Exception:
                pass
        if avec_wiki and not _cache_valide((f.get("wikidata") or {}).get("maj")):
            a_rafraichir_wd.append(fid)

    if avec_wiki and a_rafraichir_wd:
        # Priorité aux Français puis meilleurs Elo
        a_rafraichir_wd.sort(
            key=lambda x: (
                not fiches[x]["francais"],
                -(fiches[x]["elo"].get("standard") or fiches[x]["elo"].get("tournoi") or 0),
            )
        )
        wd_map = interroger_wikidata(a_rafraichir_wd[:max_wiki])
        titres_wp: list[str] = []
        for fid, wd in wd_map.items():
            fiches[fid]["wikidata"] = wd
            if wd.get("titre_wikipedia") and not _cache_valide((fiches[fid].get("wikipedia") or {}).get("maj")):
                titres_wp.append(wd["titre_wikipedia"])
        if titres_wp:
            wp_map = interroger_wikipedia_fr(titres_wp)
            for fid, wd in wd_map.items():
                twp = wd.get("titre_wikipedia")
                if twp and twp in wp_map:
                    fiches[fid]["wikipedia"] = wp_map[twp]

    # 4. Finaliser et écrire les fichiers
    os.makedirs(dossier_sortie, exist_ok=True)
    index_rows: list[dict] = []
    for fid, f in fiches.items():
        precs = f.pop("_precisions", [])
        if precs:
            f["bilan"]["precision_moy"] = round(sum(precs) / len(precs), 1)
        f["parties"].sort(key=lambda p: p.get("date", ""), reverse=True)
        f["parties"] = f["parties"][:40]
        f["tournois"].sort(key=lambda t: t.get("debut", ""), reverse=True)

        netutil.save_json_if_changed(os.path.join(dossier_sortie, f"{fid}.json"), f)
        index_rows.append({
            "id": fid,
            "nom": f["nom"],
            "fed": f["fed"],
            "drapeau": f["drapeau"],
            "francais": f["francais"],
            "titre": f["titre"],
            "elo": f["elo"].get("standard") or f["elo"].get("tournoi"),
            "rang_monde": f["rangs"].get("monde_standard"),
            "rang_france": f["rangs"].get("france_standard"),
        })

    index_rows.sort(key=lambda r: (-(r["elo"] or 0), r["nom"]))
    index = {"maj": netutil.utc_now_iso(), "joueurs": index_rows}
    netutil.save_json_if_changed(os.path.join(dossier_sortie, "index.json"), index)
    return index


def _run_tests() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        dt_dir = os.path.join(tmp, "tournois")
        os.makedirs(dt_dir)
        fide_p = os.path.join(tmp, "classement.json")
        out_dir = os.path.join(tmp, "joueurs")

        with open(fide_p, "w", encoding="utf-8") as fp:
            json.dump({
                "monde": {"standard": [{"id": "12573981", "nom": "Firouzja, Alireza", "fed": "FRA", "titre": "GM", "elo": 2763, "rang": 7}]},
                "france": {"standard": [{"id": "12573981", "nom": "Firouzja, Alireza", "fed": "FRA", "titre": "GM", "elo": 2763, "rang": 1}]},
            }, fp)
        with open(os.path.join(dt_dir, "tata26.json"), "w", encoding="utf-8") as fp:
            json.dump({
                "id": "tata26", "nom": "Tata Steel 2026", "debut": "2026-01-18",
                "classement": [{"fide_id": "12573981", "nom": "Firouzja, Alireza", "fed": "FRA", "titre": "GM", "elo": 2763, "rang": 2, "score": 8.0, "jouees": 13}],
                "rondes": [{"id": "r1", "nom": "Ronde 1", "parties": [{
                    "id": "tata26-r1-1", "date": "2026-01-18", "statut": "terminee", "resultat": "1-0", "eco": "C65",
                    "blancs": {"fide_id": "12573981", "nom": "Firouzja, Alireza", "fed": "FRA", "titre": "GM", "elo": 2763, "drapeau": "fr"},
                    "noirs": {"fide_id": "1503014", "nom": "Carlsen, Magnus", "fed": "NOR", "titre": "GM", "elo": 2832, "drapeau": "no"},
                    "analyse": {"blancs": {"precision": 95.0}, "noirs": {"precision": 88.0}},
                }]}],
            }, fp)

        idx = construire_fiches(dt_dir, fide_p, out_dir, avec_wiki=False)
        assert len(idx["joueurs"]) == 2
        with open(os.path.join(out_dir, "12573981.json"), "r", encoding="utf-8") as fp:
            f = json.load(fp)
        assert f["bilan"]["v"] == 1 and f["bilan"]["precision_moy"] == 95.0
        assert f["rangs"]["france_standard"] == 1
    print("[OK] joueurs.py : tous les tests passent.")


if __name__ == "__main__":
    if "--test" in sys.argv:
        _run_tests()
    else:
        print("Usage: python3 joueurs.py --test")
