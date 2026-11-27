#!/usr/bin/env python3
"""Orchestrateur du pipeline Échecs (Python 3.11 stdlib, sans notification ntfy).

Modes :
- python3 run_pipeline.py --tournois [--max 10] [--sf-budget 20] [--sans-ia]
- python3 run_pipeline.py --fide
- python3 run_pipeline.py --joueurs [--sans-wiki]
- python3 run_pipeline.py --demo /tmp/demo_echecs   # jeu d'essai local pour tests UI
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import agents_flash
import analyse
import fide
import historique
import joueurs
import lichess
import netutil

DOSSIER_TOURNOIS = "tournois"
DOSSIER_FIDE = "fide"
DOSSIER_JOUEURS = "joueurs"
DOSSIER_HISTORIQUE = "historique"


def reindexer_tournois(dossier_tournois: str = DOSSIER_TOURNOIS) -> dict:
    """Reconstruit tournois/index.json à partir de tous les tournois/*.json."""
    lignes: list[dict] = []
    for path in sorted(glob.glob(os.path.join(dossier_tournois, "*.json"))):
        if os.path.basename(path) == "index.json":
            continue
        try:
            with open(path, "r", encoding="utf-8") as fp:
                t = json.load(fp)
        except Exception:
            continue
        rondes = t.get("rondes") or []
        rondes_finies = [r for r in rondes if r.get("statut") == "terminee"]
        rondes_en_cours = [r for r in rondes if r.get("statut") == "en_cours"]
        derniere = rondes_en_cours[0] if rondes_en_cours else (rondes_finies[-1] if rondes_finies else (rondes[0] if rondes else None))

        # Extraire un aperçu léger des parties françaises récentes pour l'accueil
        fra_recentes = []
        for r in reversed(rondes):
            for p in r.get("parties", []):
                if p.get("est_francais") and p.get("statut") == "terminee":
                    fra_recentes.append({
                        "partie_id": p["id"],
                        "ronde_id": r["id"],
                        "ronde": r["nom"],
                        "date": p.get("date", ""),
                        "blancs": p["blancs"],
                        "noirs": p["noirs"],
                        "resultat": p["resultat"],
                        "nb_coups": p.get("nb_coups", 0),
                        "eco": p.get("eco", ""),
                        "ouverture": p.get("ouverture", ""),
                        "resume": p.get("resume", ""),
                        "resume_source": p.get("resume_source", ""),
                        "precision": {
                            "blancs": (p.get("analyse") or {}).get("blancs", {}).get("precision"),
                            "noirs": (p.get("analyse") or {}).get("noirs", {}).get("precision"),
                        } if p.get("analyse") else None,
                    })
                    if len(fra_recentes) >= 4:
                        break
            if len(fra_recentes) >= 4:
                break

        leader = t["classement"][0] if t.get("classement") else None
        lignes.append({
            "id": t["id"],
            "nom": t["nom"],
            "categorie": t.get("categorie", "autres"),
            "format": t.get("format", "swiss"),
            "tier": t.get("tier", 0),
            "statut": t.get("statut", "termine"),
            "debut": t.get("debut", ""),
            "fin": t.get("fin", ""),
            "lieu": t.get("lieu", ""),
            "cadence": t.get("cadence", ""),
            "nb_joueurs": len(t.get("classement") or []),
            "nb_francais": t.get("nb_francais", 0),
            "nb_rondes": len(rondes),
            "rondes_finies": len(rondes_finies),
            "ronde_courante": derniere["nom"] if derniere else "",
            "leader": {
                "nom": leader["nom"],
                "titre": leader.get("titre", ""),
                "drapeau": leader.get("drapeau", ""),
                "score": leader.get("score"),
            } if leader else None,
            "francais_recents": fra_recentes,
            "fichier": f"{t['id']}.json",
            "maj": t.get("maj", ""),
        })

    en_cours = [x for x in lignes if x["statut"] == "en_cours"]
    en_cours.sort(key=lambda x: (-(x["tier"] or 0), x["debut"]))
    a_venir = sorted([x for x in lignes if x["statut"] == "a_venir"], key=lambda x: x["debut"])
    termines = sorted([x for x in lignes if x["statut"] == "termine"], key=lambda x: (x["fin"] or x["debut"]), reverse=True)
    index = {"maj": netutil.utc_now_iso(), "tournois": en_cours + termines + a_venir}
    netutil.save_json_if_changed(os.path.join(dossier_tournois, "index.json"), index)
    return index


def executer_tournois(
    dossier_tournois: str = DOSSIER_TOURNOIS,
    fichier_fide: str = os.path.join(DOSSIER_FIDE, "classement.json"),
    dossier_joueurs: str = DOSSIER_JOUEURS,
    dossier_historique: str = DOSSIER_HISTORIQUE,
    *,
    max_tournois: int = 25,
    sf_budget: int = 15,
) -> None:
    os.makedirs(dossier_tournois, exist_ok=True)

    # Purger les anciens tournois hors périmètre (ex. ligues étrangères ou cadets)
    for path in sorted(glob.glob(os.path.join(dossier_tournois, "*.json"))):
        if os.path.basename(path) == "index.json":
            continue
        try:
            with open(path, "r", encoding="utf-8") as fp:
                old_t = json.load(fp)
            if not lichess.est_tournoi_majeur_otb({"name": old_t.get("nom", ""), "info": {"location": old_t.get("lieu", "")}}):
                os.remove(path)
                print(f"[NETTOYAGE] Tournoi hors périmètre retiré : {old_t.get('nom', path)}")
        except Exception:
            pass

    candidats = lichess.fetch_top_broadcasts(pages=10)
    print(f"[LICHESS] {len(candidats)} tournois OTB majeurs détectés (actifs, à venir et passés).")

    budget_sf = [sf_budget]
    traites = 0
    with analyse.StockfishRunner(depth=12) as sf:
        if sf.available:
            print(f"[STOCKFISH] Actif ({sf.version}), budget {sf_budget} parties.")
        else:
            print("[STOCKFISH] Absent : seules les évaluations Lichess seront utilisées.")

        for item in candidats[:max_tournois]:
            tid = item["tour"]["id"]
            nom = item["tour"].get("name", tid)
            path = os.path.join(dossier_tournois, f"{tid}.json")
            ancien = None
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as fp:
                        ancien = json.load(fp)
                except Exception:
                    ancien = None

            if ancien and ancien.get("statut") == "termine" and item["etat"] == "passe":
                continue

            print(f"[COLLECTE] {nom} ({tid})…")
            try:
                t = lichess.collecter_tournoi(tid, sf_runner=sf, sf_budget_parties=budget_sf)
            except Exception as e:
                print(f"[WARN] Échec collecte {tid} : {e}")
                continue

            agents_flash.enrichir_resumes_tournoi(t)
            netutil.save_json_if_changed(path, t)
            traites += 1

    idx = reindexer_tournois(dossier_tournois)
    print(f"[INDEX] {len(idx['tournois'])} tournois indexés ({traites} mis à jour).")
    if os.path.isdir(os.path.join(dossier_historique, "t")):
        historique.reindexer_historique(dossier_historique, dossier_tournois)

    try:
        j_idx = joueurs.construire_fiches(
            dossier_tournois, fichier_fide, dossier_joueurs,
            dossier_historique=dossier_historique, avec_wiki=False,
        )
        print(f"[JOUEURS] {len(j_idx['joueurs'])} fiches mises à jour.")
    except Exception as e:
        print(f"[WARN] Fiches joueurs : {e}")


def executer_saison(
    annee: int,
    dossier_tournois: str = DOSSIER_TOURNOIS,
    dossier_historique: str = DOSSIER_HISTORIQUE,
    *,
    max_tournois: int = 20,
    sf_budget: int = 0,
) -> None:
    """Collecte les parties PGN complètes des grands tournois manquants de la saison donnée."""
    os.makedirs(dossier_tournois, exist_ok=True)
    editions = historique.lister_ids_saison(annee, dossier_historique, max_ids=max_tournois * 2)
    manquants = [e for e in editions if not os.path.exists(os.path.join(dossier_tournois, f"{e['id']}.json"))]
    print(f"[SAISON {annee}] {len(manquants)} éditions majeures sans parties complètes (max {max_tournois}).")

    budget_sf = [sf_budget]
    traites = 0
    with analyse.StockfishRunner(depth=12) as sf:
        for ed in manquants[:max_tournois]:
            tid = ed["id"]
            nom = ed["nom"]
            path = os.path.join(dossier_tournois, f"{tid}.json")
            print(f"[SAISON {annee}] Collecte complète : {nom} ({tid})…")
            try:
                t = lichess.collecter_tournoi(tid, sf_runner=sf if sf_budget > 0 else None, sf_budget_parties=budget_sf)
            except Exception as e:
                print(f"[WARN] Échec saison {tid} : {e}")
                continue
            agents_flash.enrichir_resumes_tournoi(t)
            netutil.save_json_if_changed(path, t)
            traites += 1

    reindexer_tournois(dossier_tournois)
    historique.reindexer_historique(dossier_historique, dossier_tournois)
    print(f"[SAISON {annee}] {traites} tournois ajoutés dans {dossier_tournois}/.")


def executer_historique_pipeline(
    dossier_historique: str = DOSSIER_HISTORIQUE,
    dossier_tournois: str = DOSSIER_TOURNOIS,
    fichier_fide: str = os.path.join(DOSSIER_FIDE, "classement.json"),
    dossier_joueurs: str = DOSSIER_JOUEURS,
    *,
    annee_min: int = 2021,
    budget: int = 200,
    relire: bool = False,
    saison_complete: int | None = None,
    max_saison: int = 18,
) -> None:
    historique.executer_historique(
        dossier_historique, dossier_tournois,
        annee_min=annee_min, budget=budget, relire=relire,
    )
    if saison_complete:
        executer_saison(
            saison_complete, dossier_tournois, dossier_historique,
            max_tournois=max_saison, sf_budget=0,
        )
    j_idx = joueurs.construire_fiches(
        dossier_tournois, fichier_fide, dossier_joueurs,
        dossier_historique=dossier_historique, avec_wiki=False,
    )
    print(f"[JOUEURS] {len(j_idx['joueurs'])} fiches enrichies avec l'historique complet.")


def executer_fide(dossier_fide: str = DOSSIER_FIDE) -> None:
    os.makedirs(dossier_fide, exist_ok=True)
    data = fide.collecter_fide()
    path = os.path.join(dossier_fide, "classement.json")
    netutil.save_json_if_changed(path, data)
    print(f"[FIDE] Classement {data['periode']} écrit dans {path}.")


def executer_joueurs(
    dossier_tournois: str = DOSSIER_TOURNOIS,
    fichier_fide: str = os.path.join(DOSSIER_FIDE, "classement.json"),
    dossier_joueurs: str = DOSSIER_JOUEURS,
    dossier_historique: str = DOSSIER_HISTORIQUE,
    *,
    sans_wiki: bool = False,
) -> None:
    idx = joueurs.construire_fiches(
        dossier_tournois, fichier_fide, dossier_joueurs,
        dossier_historique=dossier_historique, avec_wiki=not sans_wiki,
    )
    print(f"[JOUEURS] {len(idx['joueurs'])} fiches générées dans {dossier_joueurs}/.")


def generer_demo(cible: str) -> None:
    """Génère un jeu de données de démonstration réaliste (pour essais UI Playwright locaux)."""
    dt_dir = os.path.join(cible, "tournois")
    df_dir = os.path.join(cible, "fide")
    dj_dir = os.path.join(cible, "joueurs")
    os.makedirs(dt_dir, exist_ok=True)
    os.makedirs(df_dir, exist_ok=True)
    os.makedirs(dj_dir, exist_ok=True)

    # 1. Classement FIDE mensuel réaliste
    fide_demo = {
        "periode": "2026-11",
        "source": "FIDE",
        "maj": netutil.utc_now_iso(),
        "monde": {
            "standard": [
                {"rang": 1, "id": "1503014", "nom": "Carlsen, Magnus", "fed": "NOR", "drapeau": "no", "francais": False, "titre": "GM", "elo": 2832, "parties": 12, "naissance": 1990, "sexe": "M"},
                {"rang": 2, "id": "2016192", "nom": "Nakamura, Hikaru", "fed": "USA", "drapeau": "us", "francais": False, "titre": "GM", "elo": 2802, "parties": 8, "naissance": 1987, "sexe": "M"},
                {"rang": 3, "id": "2020009", "nom": "Caruana, Fabiano", "fed": "USA", "drapeau": "us", "francais": False, "titre": "GM", "elo": 2798, "parties": 14, "naissance": 1992, "sexe": "M"},
                {"rang": 4, "id": "46616543", "nom": "Gukesh, Dommaraju", "fed": "IND", "drapeau": "in", "francais": False, "titre": "GM", "elo": 2786, "parties": 16, "naissance": 2006, "sexe": "M"},
                {"rang": 5, "id": "35009192", "nom": "Erigaisi, Arjun", "fed": "IND", "drapeau": "in", "francais": False, "titre": "GM", "elo": 2782, "parties": 15, "naissance": 2003, "sexe": "M"},
                {"rang": 6, "id": "12573981", "nom": "Firouzja, Alireza", "fed": "FRA", "drapeau": "fr", "francais": True, "titre": "GM", "elo": 2768, "parties": 13, "naissance": 2003, "sexe": "M"},
                {"rang": 7, "id": "14204118", "nom": "Abdusattorov, Nodirbek", "fed": "UZB", "drapeau": "uz", "francais": False, "titre": "GM", "elo": 2765, "parties": 11, "naissance": 2004, "sexe": "M"},
                {"rang": 8, "id": "25059530", "nom": "Praggnanandhaa, R", "fed": "IND", "drapeau": "in", "francais": False, "titre": "GM", "elo": 2758, "parties": 14, "naissance": 2005, "sexe": "M"},
                {"rang": 9, "id": "623539", "nom": "Vachier-Lagrave, Maxime", "fed": "FRA", "drapeau": "fr", "francais": True, "titre": "GM", "elo": 2736, "parties": 10, "naissance": 1990, "sexe": "M"},
            ],
            "rapid": [
                {"rang": 1, "id": "1503014", "nom": "Carlsen, Magnus", "fed": "NOR", "drapeau": "no", "francais": False, "titre": "GM", "elo": 2825, "parties": 18, "naissance": 1990, "sexe": "M"},
                {"rang": 2, "id": "12573981", "nom": "Firouzja, Alireza", "fed": "FRA", "drapeau": "fr", "francais": True, "titre": "GM", "elo": 2772, "parties": 15, "naissance": 2003, "sexe": "M"},
                {"rang": 3, "id": "623539", "nom": "Vachier-Lagrave, Maxime", "fed": "FRA", "drapeau": "fr", "francais": True, "titre": "GM", "elo": 2755, "parties": 12, "naissance": 1990, "sexe": "M"},
            ],
            "blitz": [
                {"rang": 1, "id": "1503014", "nom": "Carlsen, Magnus", "fed": "NOR", "drapeau": "no", "francais": False, "titre": "GM", "elo": 2886, "parties": 24, "naissance": 1990, "sexe": "M"},
                {"rang": 2, "id": "12573981", "nom": "Firouzja, Alireza", "fed": "FRA", "drapeau": "fr", "francais": True, "titre": "GM", "elo": 2864, "parties": 20, "naissance": 2003, "sexe": "M"},
                {"rang": 3, "id": "2016192", "nom": "Nakamura, Hikaru", "fed": "USA", "drapeau": "us", "francais": False, "titre": "GM", "elo": 2860, "parties": 16, "naissance": 1987, "sexe": "M"},
            ],
        },
        "france": {
            "standard": [
                {"rang": 1, "id": "12573981", "nom": "Firouzja, Alireza", "fed": "FRA", "drapeau": "fr", "francais": True, "titre": "GM", "elo": 2768, "parties": 13, "naissance": 2003, "sexe": "M"},
                {"rang": 2, "id": "623539", "nom": "Vachier-Lagrave, Maxime", "fed": "FRA", "drapeau": "fr", "francais": True, "titre": "GM", "elo": 2736, "parties": 10, "naissance": 1990, "sexe": "M"},
                {"rang": 3, "id": "605506", "nom": "Bacrot, Etienne", "fed": "FRA", "drapeau": "fr", "francais": True, "titre": "GM", "elo": 2652, "parties": 9, "naissance": 1983, "sexe": "M"},
                {"rang": 4, "id": "36085755", "nom": "Maurizzi, Marc'Andria", "fed": "FRA", "drapeau": "fr", "francais": True, "titre": "GM", "elo": 2634, "parties": 18, "naissance": 2007, "sexe": "M"},
                {"rang": 5, "id": "629065", "nom": "Moussard, Jules", "fed": "FRA", "drapeau": "fr", "francais": True, "titre": "GM", "elo": 2628, "parties": 11, "naissance": 1995, "sexe": "M"},
            ],
            "rapid": [
                {"rang": 1, "id": "12573981", "nom": "Firouzja, Alireza", "fed": "FRA", "drapeau": "fr", "francais": True, "titre": "GM", "elo": 2772, "parties": 15, "naissance": 2003, "sexe": "M"},
                {"rang": 2, "id": "623539", "nom": "Vachier-Lagrave, Maxime", "fed": "FRA", "drapeau": "fr", "francais": True, "titre": "GM", "elo": 2755, "parties": 12, "naissance": 1990, "sexe": "M"},
            ],
            "blitz": [
                {"rang": 1, "id": "12573981", "nom": "Firouzja, Alireza", "fed": "FRA", "drapeau": "fr", "francais": True, "titre": "GM", "elo": 2864, "parties": 20, "naissance": 2003, "sexe": "M"},
                {"rang": 2, "id": "623539", "nom": "Vachier-Lagrave, Maxime", "fed": "FRA", "drapeau": "fr", "francais": True, "titre": "GM", "elo": 2782, "parties": 14, "naissance": 1990, "sexe": "M"},
            ],
        },
        "femmes": {
            "standard": [
                {"rang": 1, "id": "8602980", "nom": "Hou, Yifan", "fed": "CHN", "drapeau": "cn", "francais": False, "titre": "GM", "elo": 2633, "parties": 4, "naissance": 1994, "sexe": "F"},
                {"rang": 2, "id": "8603006", "nom": "Ju, Wenjun", "fed": "CHN", "drapeau": "cn", "francais": False, "titre": "GM", "elo": 2566, "parties": 12, "naissance": 1991, "sexe": "F"},
            ],
            "rapid": [],
            "blitz": [],
        },
        "juniors": {
            "standard": [
                {"rang": 1, "id": "46616543", "nom": "Gukesh, Dommaraju", "fed": "IND", "drapeau": "in", "francais": False, "titre": "GM", "elo": 2786, "parties": 16, "naissance": 2006, "sexe": "M"},
                {"rang": 2, "id": "36085755", "nom": "Maurizzi, Marc'Andria", "fed": "FRA", "drapeau": "fr", "francais": True, "titre": "GM", "elo": 2634, "parties": 18, "naissance": 2007, "sexe": "M"},
            ],
        },
        "stats": {"standard": {"total": 485000, "actifs": 214000}},
    }
    netutil.save_json_if_changed(os.path.join(df_dir, "classement.json"), fide_demo)

    # 2. Tournoi majeur OTB de démonstration avec parties PGN réelles + eval + clk
    joueurs_t = lichess.normaliser_joueurs([
        {"name": "Firouzja, Alireza", "fideId": 12573981, "fed": "FRA", "title": "GM", "rating": 2768, "score": 6.5, "played": 9, "performance": 2841, "ratingDiff": 9},
        {"name": "Carlsen, Magnus", "fideId": 1503014, "fed": "NOR", "title": "GM", "rating": 2832, "score": 6.5, "played": 9, "performance": 2835, "ratingDiff": 1},
        {"name": "Gukesh, Dommaraju", "fideId": 46616543, "fed": "IND", "title": "GM", "rating": 2786, "score": 5.5, "played": 9, "performance": 2790, "ratingDiff": 2},
        {"name": "Vachier-Lagrave, Maxime", "fideId": 623539, "fed": "FRA", "title": "GM", "rating": 2736, "score": 5.0, "played": 9, "performance": 2768, "ratingDiff": 4},
        {"name": "Caruana, Fabiano", "fideId": 2020009, "fed": "USA", "title": "GM", "rating": 2798, "score": 4.5, "played": 9, "performance": 2750, "ratingDiff": -6},
        {"name": "Erigaisi, Arjun", "fideId": 35009192, "fed": "IND", "title": "GM", "rating": 2782, "score": 4.0, "played": 9, "performance": 2718, "ratingDiff": -8},
    ])

    pgn_r9 = """
[Event "Sinquefield Cup 2026"]
[Site "Saint Louis"]
[Date "2026.11.25"]
[Round "9.1"]
[White "Firouzja, Alireza"]
[Black "Caruana, Fabiano"]
[Result "1-0"]
[WhiteFideId "12573981"]
[BlackFideId "2020009"]
[WhiteElo "2768"]
[BlackElo "2798"]
[ECO "C50"]
[Opening "Partie italienne : Giuoco Pianissimo"]
[TimeControl "5400+30"]

1. e4 { [%eval 0.22] [%clk 1:30:00] } 1... e5 { [%eval 0.20] [%clk 1:30:00] }
2. Nf3 { [%eval 0.21] [%clk 1:29:48] } 2... Nc6 { [%eval 0.24] [%clk 1:29:45] }
3. Bc4 { [%eval 0.18] [%clk 1:29:35] } 3... Bc5 { [%eval 0.20] [%clk 1:29:20] }
4. d3 { [%eval 0.16] [%clk 1:29:10] } 4... Nf6 { [%eval 0.18] [%clk 1:28:55] }
5. c3 { [%eval 0.15] [%clk 1:28:30] } 5... d6 { [%eval 0.20] [%clk 1:27:40] }
6. O-O { [%eval 0.17] [%clk 1:27:50] } 6... a6 { [%eval 0.22] [%clk 1:26:10] }
7. Re1 { [%eval 0.19] [%clk 1:26:20] } 7... Ba7 { [%eval 0.21] [%clk 1:24:30] }
8. h3 { [%eval 0.20] [%clk 1:24:00] } 8... h6 { [%eval 0.25] [%clk 1:21:15] }
9. Nbd2 { [%eval 0.24] [%clk 1:20:40] } 9... O-O { [%eval 0.28] [%clk 1:18:00] }
10. Nf1 { [%eval 0.30] [%clk 1:17:10] } 10... Re8 { [%eval 0.32] [%clk 1:12:45] }
11. Ng3 { [%eval 0.35] [%clk 1:14:00] } 11... Be6 { [%eval 0.40] [%clk 1:05:20] }
12. Bb3 { [%eval 0.38] [%clk 1:10:15] } 12... d5 { [%eval 0.55] [%clk 0:54:10] }
13. exd5 { [%eval 0.52] [%clk 1:06:00] } 13... Nxd5 { [%eval 1.85] [%clk 0:42:30] }
14. Nxe5 { [%eval 1.90] [%clk 0:58:20] } 14... Nxe5 { [%eval 2.10] [%clk 0:31:15] }
15. Rxe5 { [%eval 2.05] [%clk 0:55:10] } 15... c6 { [%eval 3.90] [%clk 0:12:40] }
16. d4 { [%eval 3.85] [%clk 0:49:30] } 16... Bb8 { [%eval 4.60] [%clk 0:00:52] }
17. Re1 { [%eval 4.55] [%clk 0:45:00] } 1-0

[Event "Sinquefield Cup 2026"]
[Site "Saint Louis"]
[Date "2026.11.25"]
[Round "9.2"]
[White "Carlsen, Magnus"]
[Black "Vachier-Lagrave, Maxime"]
[Result "1/2-1/2"]
[WhiteFideId "1503014"]
[BlackFideId "623539"]
[WhiteElo "2832"]
[BlackElo "2736"]
[ECO "B90"]
[Opening "Défense sicilienne : variante Najdorf"]
[TimeControl "5400+30"]

1. e4 { [%eval 0.20] [%clk 1:30:00] } 1... c5 { [%eval 0.25] [%clk 1:30:00] }
2. Nf3 { [%eval 0.22] [%clk 1:29:50] } 2... d6 { [%eval 0.26] [%clk 1:29:45] }
3. d4 { [%eval 0.25] [%clk 1:29:30] } 3... cxd4 { [%eval 0.24] [%clk 1:29:35] }
4. Nxd4 { [%eval 0.25] [%clk 1:29:20] } 4... Nf6 { [%eval 0.28] [%clk 1:29:20] }
5. Nc3 { [%eval 0.24] [%clk 1:29:05] } 5... a6 { [%eval 0.30] [%clk 1:29:00] }
6. Be3 { [%eval 0.28] [%clk 1:28:10] } 6... e5 { [%eval 0.32] [%clk 1:28:15] }
7. Nb3 { [%eval 0.26] [%clk 1:27:00] } 7... Be6 { [%eval 0.22] [%clk 1:27:10] }
8. f3 { [%eval 0.20] [%clk 1:25:00] } 8... Be7 { [%eval 0.18] [%clk 1:25:30] }
9. Qd2 { [%eval 0.15] [%clk 1:22:00] } 9... O-O { [%eval 0.12] [%clk 1:22:40] }
10. O-O-O { [%eval 0.10] [%clk 1:18:00] } 10... Nbd7 { [%eval 0.08] [%clk 1:19:00] }
11. g4 { [%eval 0.05] [%clk 1:12:00] } 11... b5 { [%eval 0.02] [%clk 1:13:00] }
12. g5 { [%eval 0.00] [%clk 1:05:00] } 12... b4 { [%eval 0.00] [%clk 1:08:00] } 1/2-1/2
"""
    parties_r9 = lichess.construire_parties_ronde(pgn_r9, "sinq26", "r9", "Ronde 9", joueurs_t)
    tournoi_demo = {
        "id": "sinq26",
        "nom": "Sinquefield Cup 2026",
        "slug": "sinquefield-cup-2026",
        "categorie": "super",
        "format": "round_robin",
        "tier": 5,
        "statut": "en_cours",
        "debut": "2026-11-17",
        "fin": "2026-11-27",
        "lieu": "Saint-Louis, États-Unis",
        "cadence": "90 min + 30 s/coup",
        "cadence_fide": "standard",
        "site": "https://grandchesstour.org",
        "url_lichess": "https://lichess.org/broadcast",
        "source": "Lichess",
        "nb_francais": 2,
        "classement": joueurs_t,
        "rondes": [
            {"id": "r9", "nom": "Ronde 9", "statut": "terminee", "debut": "2026-11-25T18:00:00Z", "parties": parties_r9},
            {"id": "r10", "nom": "Ronde 10", "statut": "a_venir", "debut": "2026-11-27T18:00:00Z", "parties": []},
        ],
        "maj": netutil.utc_now_iso(),
    }
    agents_flash.enrichir_resumes_tournoi(tournoi_demo)
    netutil.save_json_if_changed(os.path.join(dt_dir, "sinq26.json"), tournoi_demo)
    reindexer_tournois(dt_dir)

    # 3. Historique de démonstration (2024–2026) pour les essais UI
    dh_dir = os.path.join(cible, "historique")
    os.makedirs(os.path.join(dh_dir, "t"), exist_ok=True)
    ed_demo = {
        "id": "tata25",
        "nom": "Tata Steel Chess 2025 | Masters",
        "categorie": "super",
        "format": "round_robin",
        "tier": 5,
        "annee": 2025,
        "statut": "termine",
        "debut": "2025-01-18",
        "fin": "2025-02-02",
        "lieu": "Wijk aan Zee, Pays-Bas",
        "cadence": "100 min + 30 s",
        "nb_joueurs": 6,
        "nb_francais": 2,
        "vainqueur": {"rang": 1, "nom": "Firouzja, Alireza", "fide_id": "12573981", "fed": "FRA", "drapeau": "fr", "titre": "GM", "score": 8.5, "jouees": 13},
        "podium": [
            {"rang": 1, "nom": "Firouzja, Alireza", "fide_id": "12573981", "fed": "FRA", "drapeau": "fr", "titre": "GM", "score": 8.5, "jouees": 13},
            {"rang": 2, "nom": "Carlsen, Magnus", "fide_id": "1503014", "fed": "NOR", "drapeau": "no", "titre": "GM", "score": 8.0, "jouees": 13},
            {"rang": 3, "nom": "Gukesh, Dommaraju", "fide_id": "46616543", "fed": "IND", "drapeau": "in", "titre": "GM", "score": 7.5, "jouees": 13},
        ],
        "classement": joueurs_t,
        "maj": netutil.utc_now_iso(),
    }
    netutil.save_json_if_changed(os.path.join(dh_dir, "t", "tata25.json"), ed_demo)
    historique.reindexer_historique(dh_dir, dt_dir)
    joueurs.construire_fiches(dt_dir, os.path.join(df_dir, "classement.json"), dj_dir, dossier_historique=dh_dir, avec_wiki=False)
    print(f"[DEMO] Jeu d'essai écrit dans {cible}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Pipeline Dashboard Échecs")
    ap.add_argument("--tournois", action="store_true", help="Collecter les tournois OTB majeurs récents (Lichess)")
    ap.add_argument("--historique", action="store_true", help="Collecter l'historique multi-années des tournois majeurs")
    ap.add_argument("--saison", type=int, default=0, help="Collecter les parties PGN complètes des tournois majeurs de cette année (ex. 2026)")
    ap.add_argument("--annee-min", type=int, default=2021, help="Année de départ de la collecte historique (défaut: 2021)")
    ap.add_argument("--budget-historique", type=int, default=200, help="Nombre maximal d'éditions historiques par passage")
    ap.add_argument("--relire", action="store_true", help="Re-collecter même les éditions historiques déjà en cache")
    ap.add_argument("--fide", action="store_true", help="Mettre à jour le classement mensuel FIDE")
    ap.add_argument("--joueurs", action="store_true", help="Régénérer les fiches joueurs (+ Wikidata/Wikipédia)")
    ap.add_argument("--max", type=int, default=12, help="Nombre maximal de tournois complets collectés")
    ap.add_argument("--sf-budget", type=int, default=20, help="Budget Stockfish (parties par passage)")
    ap.add_argument("--sans-wiki", action="store_true", help="Désactiver Wikidata/Wikipédia")
    ap.add_argument("--demo", metavar="DIR", help="Générer un jeu d'essai local dans DIR")
    args = ap.parse_args()

    if args.demo:
        generer_demo(args.demo)
        return 0

    if not (args.tournois or args.historique or args.saison or args.fide or args.joueurs):
        args.tournois = True

    if args.fide:
        executer_fide()
    if args.historique:
        executer_historique_pipeline(
            annee_min=args.annee_min,
            budget=args.budget_historique,
            relire=args.relire,
            saison_complete=args.saison or None,
            max_saison=args.max,
        )
    elif args.saison:
        executer_saison(args.saison, max_tournois=args.max, sf_budget=0)
    if args.tournois:
        executer_tournois(max_tournois=args.max, sf_budget=args.sf_budget)
    if args.joueurs:
        executer_joueurs(sans_wiki=args.sans_wiki)
    return 0


if __name__ == "__main__":
    sys.exit(main())
