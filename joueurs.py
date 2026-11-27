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


def _normaliser_photo_commons(url: str) -> str:
    if not url:
        return ""
    u = url.replace("http://", "https://", 1)
    if "Special:FilePath" in u and "width=" not in u:
        u += ("&" if "?" in u else "?") + "width=240"
    return u


def interroger_wikidata(fide_ids: list[str]) -> dict[str, dict]:
    """Interroge Wikidata par lots via P1440 (identifiant FIDE) : naissance, photo Commons HTTPS, Wikipédia FR."""
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
                timeout=35,
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
            img_url = _normaliser_photo_commons((r.get("image") or {}).get("value", ""))
            result[fid] = {
                "qid": qid,
                "naissance": dob if len(dob) == 10 else "",
                "lieu_naissance": (r.get("lieuLabel") or {}).get("value", ""),
                "photo_commons": img_url,
                "titre_wikipedia": titre_wp,
                "maj": netutil.utc_now_iso(),
            }
    return result


def interroger_palmares_wikidata(fide_ids: list[str]) -> dict[str, list[dict]]:
    """Collecte le palmarès vérifié (tournois d'échecs remportés, P1346 + P641=Q718) via Wikidata."""
    palmares: dict[str, list[dict]] = {}
    for i in range(0, len(fide_ids), 60):
        lot = [fid for fid in fide_ids[i : i + 60] if fid and fid.isdigit()]
        if not lot:
            continue
        values = " ".join(f'"{fid}"' for fid in lot)
        query = f"""
        SELECT DISTINCT ?fide ?eventLabel ?date WHERE {{
          VALUES ?fide {{ {values} }}
          ?item wdt:P1440 ?fide .
          ?event wdt:P1346 ?item ;
                 wdt:P641 wd:Q718 .
          OPTIONAL {{ ?event wdt:P585 ?d1 . }}
          OPTIONAL {{ ?event wdt:P580 ?d2 . }}
          BIND(COALESCE(?d1, ?d2) AS ?date)
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "fr,en". }}
        }}
        """
        url = "https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(query)
        try:
            data = netutil.fetch_json(
                url,
                headers={"Accept": "application/sparql-results+json"},
                min_interval=1.5,
                timeout=35,
            )
        except Exception as e:
            print(f"[WARN] Palmarès Wikidata lot {i} : {e}")
            break
        rows = (data.get("results") or {}).get("bindings", []) if isinstance(data, dict) else []
        seen_per_player: dict[str, set[str]] = {}
        for r in rows:
            fid = (r.get("fide") or {}).get("value", "")
            titre = (r.get("eventLabel") or {}).get("value", "").strip()
            if not fid or not titre or titre.startswith("Q"):
                continue
            date_raw = (r.get("date") or {}).get("value", "")
            annee = int(date_raw[:4]) if len(date_raw) >= 4 and date_raw[:4].isdigit() else None
            key = f"{annee or ''}|{titre.lower()}"
            s = seen_per_player.setdefault(fid, set())
            if key in s:
                continue
            s.add(key)
            palmares.setdefault(fid, []).append({"titre": titre, "annee": annee})

    for fid, items in palmares.items():
        items.sort(key=lambda x: (-(x["annee"] or 0), x["titre"]))
        palmares[fid] = items[:30]
    return palmares


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


def _variantes_nom(nom: str) -> list[str]:
    """Retourne les formes normalisées d'un nom ('Nom, Prénom' et 'Prénom Nom')."""
    out: list[str] = []
    n = netutil.normalize(nom)
    if n:
        out.append(n)
    if "," in nom:
        parts = [s.strip() for s in nom.split(",", 1)]
        if parts[0] and parts[1]:
            inv = netutil.normalize(f"{parts[1]} {parts[0]}")
            if inv and inv not in out:
                out.append(inv)
    elif " " in nom.strip():
        parts = nom.strip().split()
        if len(parts) == 2:
            inv = netutil.normalize(f"{parts[1]} {parts[0]}")
            if inv and inv not in out:
                out.append(inv)
    return out


def construire_fiches(
    dossier_tournois: str,
    fichier_fide: str,
    dossier_sortie: str,
    *,
    dossier_historique: str = "historique",
    avec_wiki: bool = True,
    max_wiki: int = 250,
) -> dict:
    """Construit toutes les fiches joueurs et joueurs/index.json (tournois + historique + FIDE + Wikidata)."""
    fiches: dict[str, dict] = {}
    nom_vers_fid: dict[str, str] = {}

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
                "_resultats_map": {},
                "tournois": [],
                "resultats_majeurs": [],
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
        for v in _variantes_nom(nom):
            nom_vers_fid.setdefault(v, fid)
        return f

    # 1. Charger les classements FIDE (Top Monde + Top France + Femmes)
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

    # 2. Agréger les tournois collectés complets (tournois/*.json)
    tournois_complets: set[str] = set()
    for path in sorted(glob.glob(os.path.join(dossier_tournois, "*.json"))):
        if os.path.basename(path) == "index.json":
            continue
        with open(path, "r", encoding="utf-8") as fp:
            t = json.load(fp)
        tid = t["id"]
        tournois_complets.add(tid)
        tnom = t["nom"]
        debut = t.get("debut", "")
        annee = int(debut[:4]) if len(debut) >= 4 and debut[:4].isdigit() else None
        nb_j = len(t.get("classement") or [])
        cat = t.get("categorie", "")

        for j in t.get("classement", []):
            fid = j.get("fide_id", "")
            if not fid:
                continue
            f = _get(fid, j["nom"], j.get("fed", ""), j.get("titre", ""))
            if j.get("elo") and "standard" not in f["elo"]:
                f["elo"]["tournoi"] = j["elo"]
            rec_t = {
                "id": tid,
                "nom": tnom,
                "categorie": cat,
                "annee": annee,
                "debut": debut,
                "rang": j.get("rang"),
                "nb_joueurs": nb_j,
                "score": j.get("score"),
                "jouees": j.get("jouees"),
                "perf": j.get("perf"),
                "a_parties": True,
            }
            f["tournois"].append(rec_t)
            f["_resultats_map"][tid] = rec_t

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

    # 2b. Agréger l'historique multi-années des tournois majeurs (historique/t/*.json)
    hist_dir = os.path.join(dossier_historique, "t") if dossier_historique else ""
    if hist_dir and os.path.isdir(hist_dir):
        for path in sorted(glob.glob(os.path.join(hist_dir, "*.json"))):
            try:
                with open(path, "r", encoding="utf-8") as fp:
                    ed = json.load(fp)
            except Exception:
                continue
            tid = ed.get("id", "")
            if not tid:
                continue
            ed_nom = ed.get("nom", "")
            debut = ed.get("debut", "")
            annee = ed.get("annee") or (int(debut[:4]) if len(debut) >= 4 and debut[:4].isdigit() else None)
            nb_j = ed.get("nb_joueurs") or len(ed.get("classement") or [])
            cat = ed.get("categorie", "")
            for j in ed.get("classement") or []:
                fid = str(j.get("fide_id") or "").strip()
                if not fid:
                    for v in _variantes_nom(j.get("nom", "")):
                        if v in nom_vers_fid:
                            fid = nom_vers_fid[v]
                            break
                if not fid:
                    continue
                f = _get(fid, j.get("nom", ""), j.get("fed", ""), j.get("titre", ""))
                if j.get("elo") and "standard" not in f["elo"] and "tournoi" not in f["elo"]:
                    f["elo"]["tournoi"] = j["elo"]
                if tid in f["_resultats_map"]:
                    continue
                f["_resultats_map"][tid] = {
                    "id": tid,
                    "nom": ed_nom,
                    "categorie": cat,
                    "annee": annee,
                    "debut": debut,
                    "rang": j.get("rang"),
                    "nb_joueurs": nb_j,
                    "score": j.get("score"),
                    "jouees": j.get("jouees"),
                    "perf": j.get("perf"),
                    "a_parties": tid in tournois_complets,
                }

    # 3. Recharger le cache Wikidata/Wikipédia/Palmarès existant et rafraîchir si besoin
    a_rafraichir_wd: list[str] = []
    a_rafraichir_pal: list[str] = []
    for fid, f in fiches.items():
        old_path = os.path.join(dossier_sortie, f"{fid}.json")
        if os.path.exists(old_path):
            try:
                with open(old_path, "r", encoding="utf-8") as fp:
                    old = json.load(fp)
                if old.get("wikidata"):
                    wd_old = dict(old["wikidata"])
                    if wd_old.get("photo_commons"):
                        wd_old["photo_commons"] = _normaliser_photo_commons(wd_old["photo_commons"])
                    f["wikidata"] = wd_old
                if old.get("wikipedia"):
                    f["wikipedia"] = old["wikipedia"]
                if "palmares" in old:
                    f["palmares"] = old["palmares"]
                    if old.get("palmares_maj"):
                        f["palmares_maj"] = old["palmares_maj"]
            except Exception:
                pass
        if avec_wiki and not _cache_valide((f.get("wikidata") or {}).get("maj")):
            a_rafraichir_wd.append(fid)
        if avec_wiki and not _cache_valide(f.get("palmares_maj")):
            a_rafraichir_pal.append(fid)

    def _prio_elo(x: str) -> tuple:
        return (-(fiches[x]["elo"].get("standard") or fiches[x]["elo"].get("tournoi") or 0), not fiches[x]["francais"])

    if avec_wiki and a_rafraichir_wd:
        a_rafraichir_wd.sort(key=_prio_elo)
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

    if avec_wiki and a_rafraichir_pal:
        a_rafraichir_pal.sort(key=_prio_elo)
        lot_pal = a_rafraichir_pal[:max_wiki]
        pal_map = interroger_palmares_wikidata(lot_pal)
        now_iso = netutil.utc_now_iso()
        for fid in lot_pal:
            fiches[fid]["palmares"] = pal_map.get(fid, [])
            fiches[fid]["palmares_maj"] = now_iso

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

        res_map = f.pop("_resultats_map", {})
        res_list = sorted(res_map.values(), key=lambda x: (x.get("debut") or "", x.get("nom") or ""), reverse=True)
        f["resultats_majeurs"] = res_list

        # Filtrer les profils purement historiques très éloignés du haut niveau (ex. table 200 d'un open)
        elo_ref = f["elo"].get("standard") or f["elo"].get("tournoi") or 0
        dans_fide = bool(f["rangs"])
        dans_saison = bool(f["tournois"] or f["parties"])
        if not (dans_fide or dans_saison or f["francais"] or elo_ref >= 2500 or len(res_list) >= 2):
            continue

        # Statistiques déterministes de carrière sur les tournois majeurs collectés
        rangs_valides = [r["rang"] for r in res_list if isinstance(r.get("rang"), int) and r["rang"] > 0]
        perfs_valides = [r["perf"] for r in res_list if isinstance(r.get("perf"), (int, float)) and r["perf"] > 0]
        f["carriere"] = {
            "tournois": len(res_list),
            "victoires": sum(1 for rg in rangs_valides if rg == 1),
            "podiums": sum(1 for rg in rangs_valides if rg <= 3),
            "top10": sum(1 for rg in rangs_valides if rg <= 10),
            "meilleur_rang": min(rangs_valides) if rangs_valides else None,
            "perf_moy": round(sum(perfs_valides) / len(perfs_valides)) if perfs_valides else None,
        }

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
            "nb_majeurs": len(res_list),
            "victoires": f["carriere"]["victoires"],
            "podiums": f["carriere"]["podiums"],
        })

    index_rows.sort(key=lambda r: (-(r["elo"] or 0), r["nom"]))
    index = {"maj": netutil.utc_now_iso(), "joueurs": index_rows}
    netutil.save_json_if_changed(os.path.join(dossier_sortie, "index.json"), index)
    return index


def _run_tests() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        dt_dir = os.path.join(tmp, "tournois")
        hist_t_dir = os.path.join(tmp, "historique", "t")
        os.makedirs(dt_dir)
        os.makedirs(hist_t_dir)
        fide_p = os.path.join(tmp, "classement.json")
        out_dir = os.path.join(tmp, "joueurs")

        with open(fide_p, "w", encoding="utf-8") as fp:
            json.dump({
                "monde": {"standard": [{"id": "12573981", "nom": "Firouzja, Alireza", "fed": "FRA", "titre": "GM", "elo": 2763, "rang": 7}]},
                "france": {"standard": [{"id": "12573981", "nom": "Firouzja, Alireza", "fed": "FRA", "titre": "GM", "elo": 2763, "rang": 1}]},
            }, fp)
        with open(os.path.join(dt_dir, "tata26.json"), "w", encoding="utf-8") as fp:
            json.dump({
                "id": "tata26", "nom": "Tata Steel 2026", "categorie": "super", "debut": "2026-01-18",
                "classement": [{"fide_id": "12573981", "nom": "Firouzja, Alireza", "fed": "FRA", "titre": "GM", "elo": 2763, "rang": 2, "score": 8.0, "jouees": 13, "perf": 2810}],
                "rondes": [{"id": "r1", "nom": "Ronde 1", "parties": [{
                    "id": "tata26-r1-1", "date": "2026-01-18", "statut": "terminee", "resultat": "1-0", "eco": "C65",
                    "blancs": {"fide_id": "12573981", "nom": "Firouzja, Alireza", "fed": "FRA", "titre": "GM", "elo": 2763, "drapeau": "fr"},
                    "noirs": {"fide_id": "1503014", "nom": "Carlsen, Magnus", "fed": "NOR", "titre": "GM", "elo": 2832, "drapeau": "no"},
                    "analyse": {"blancs": {"precision": 95.0}, "noirs": {"precision": 88.0}},
                }]}],
            }, fp)
        with open(os.path.join(hist_t_dir, "sinq24.json"), "w", encoding="utf-8") as fp:
            json.dump({
                "id": "sinq24", "nom": "Sinquefield Cup 2024", "categorie": "super", "annee": 2024, "debut": "2024-08-19", "nb_joueurs": 10,
                "classement": [{"fide_id": "", "nom": "Alireza Firouzja", "fed": "FRA", "titre": "GM", "elo": 2751, "rang": 1, "score": 6.0, "jouees": 9, "perf": 2860}],
            }, fp)

        idx = construire_fiches(
            dt_dir, fide_p, out_dir, dossier_historique=os.path.join(tmp, "historique"), avec_wiki=False
        )
        assert len(idx["joueurs"]) == 2
        with open(os.path.join(out_dir, "12573981.json"), "r", encoding="utf-8") as fp:
            f = json.load(fp)
        assert f["bilan"]["v"] == 1 and f["bilan"]["precision_moy"] == 95.0
        assert f["rangs"]["france_standard"] == 1
        assert len(f["resultats_majeurs"]) == 2
        assert f["carriere"]["victoires"] == 1 and f["carriere"]["podiums"] == 2 and f["carriere"]["meilleur_rang"] == 1
    print("[OK] joueurs.py : tous les tests passent.")


if __name__ == "__main__":
    if "--test" in sys.argv:
        _run_tests()
    else:
        print("Usage: python3 joueurs.py --test")
