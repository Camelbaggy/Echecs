#!/usr/bin/env python3
"""Collecteur d'historique des tournois OTB majeurs du circuit (2021–2026+).

Interroge l'API officielle Lichess Broadcasts Search (/api/broadcast/search)
et récupère les classements complets (/broadcast/{id}/players) de chaque édition
majeure pour :
1. Afficher tous les tournois majeurs du circuit sur le Dashboard (saison en cours + archives).
2. Calculer le palmarès complet de carrière de chaque joueur (victoires, podiums,
   top 10 et l'ensemble de ses résultats en tournois majeurs).

Stockage léger et incrémental :
- historique/t/<id>.json : métadonnées + classement complet d'une édition.
- historique/index.json  : index léger (métadonnées + vainqueur + podium) chargé par index.html.
"""

from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
import sys
import urllib.parse

import lichess
import netutil

DOSSIER_HISTORIQUE = "historique"

# Requêtes couvrant l'ensemble des grands circuits OTB (FIDE, Super-tournois, Europe, France)
REQUETES_CIRCUIT: list[str] = [
    # FIDE
    "World Chess Championship",
    "FIDE Candidates",
    "FIDE World Cup",
    "FIDE Grand Swiss",
    "FIDE Chess Olympiad",
    "FIDE World Rapid",
    "FIDE World Blitz",
    "FIDE Grand Prix",
    "Women's Grand Prix",
    # Super-tournois & Grand Chess Tour
    "Tata Steel Chess",
    "Tata Steel Masters",
    "Norway Chess",
    "Sinquefield Cup",
    "Superbet Chess Classic",
    "Superbet Rapid",
    "Grand Chess Tour",
    "Prague International Chess",
    "Grenke Chess",
    "Freestyle Chess",
    "Biel Chess",
    "Dortmund",
    "London Chess Classic",
    "Saint Louis Rapid",
    "American Cup",
    "Cairns Cup",
    "UzChess",
    "Chennai Grand Masters",
    "WR Chess Masters",
    "Sharjah Masters",
    "Qatar Masters",
    "Reykjavik Open",
    "TePe Sigeman",
    "U.S. Championship",
    "US Masters",
    "Aeroflot Open",
    # Europe
    "European Individual Chess Championship",
    "European Women Chess Championship",
    "European Team Chess Championship",
    "European Club Cup",
    # France
    "Championnat de France",
    "French Championship",
    "Top 16",
    "Cappelle-la-Grande",
]

# Fragments à ignorer (broadcasts mono-ronde anciens, playoffs isolés, tables secondaires)
_RE_FRAGMENT = re.compile(
    r"(?:\bround\s+\d+\b|\bgame\s+\d+\b|\bplayoff\b|\btiebreak\b|\barmageddon\b|"
    r"\bultimate\s+moves\b|\bwild\s*card\b|\bqualifier\b|\bqualifying\b|"
    r"\blower\s+boards\b|\bboards\s+(?!1\b)\d+|\bmatches\s+(?!1\b)\d+|\bpoule\s+basse\b|\belimination\b|"
    r"\bopen\s+b\b|\bopen\s+c\b|\bgroup\s+[b-z]\b|\bchallengers\s+b\b)",
    re.IGNORECASE,
)


def est_edition_historique_valide(tour: dict, *, annee_min: int = 2021) -> bool:
    """Vérifie qu'un broadcast est une édition OTB majeure complète (pas un fragment)."""
    if not lichess.est_tournoi_majeur_otb(tour):
        return False
    nom = tour.get("name", "")
    if _RE_FRAGMENT.search(nom):
        return False
    cat = lichess.categorie_tournoi(tour)
    tier = tour.get("tier") or 0
    tier_min = 3 if cat in ("france", "europe") else 4
    if tier < tier_min:
        return False
    dates = tour.get("dates") or []
    if dates:
        annee = dt.datetime.fromtimestamp(dates[0] / 1000.0, tz=dt.timezone.utc).year
        if annee < annee_min:
            return False
    return True


def rechercher_broadcasts_circuit(
    requetes: list[str] | None = None,
    *,
    pages_par_requete: int = 2,
    annee_min: int = 2021,
) -> list[dict]:
    """Interroge /api/broadcast/search pour recenser toutes les éditions majeures du circuit."""
    vues: dict[str, dict] = {}
    for q in (requetes or REQUETES_CIRCUIT):
        for page in range(1, pages_par_requete + 1):
            url = (
                f"{lichess.API_BASE}/api/broadcast/search"
                f"?q={urllib.parse.quote(q)}&page={page}"
            )
            try:
                data = netutil.fetch_json(url, min_interval=0.7, timeout=20)
            except Exception as e:
                print(f"[WARN] Recherche Lichess '{q}' p.{page} : {e}")
                break
            items = data.get("currentPageResults") or [] if isinstance(data, dict) else []
            if not items:
                break
            trop_ancien = False
            for it in items:
                tour = it.get("tour") or {}
                tid = tour.get("id")
                if not tid or tid in vues:
                    continue
                dates = tour.get("dates") or []
                if dates:
                    yr = dt.datetime.fromtimestamp(dates[0] / 1000.0, tz=dt.timezone.utc).year
                    if yr < annee_min:
                        trop_ancien = True
                        continue
                if est_edition_historique_valide(tour, annee_min=annee_min):
                    vues[tid] = tour
            if not data.get("nextPage") or trop_ancien:
                break

    # Trier du plus récent au plus ancien
    res = list(vues.values())
    res.sort(key=lambda t: (t.get("dates") or [0])[0], reverse=True)
    return res


def collecter_edition_historique(tour: dict) -> dict | None:
    """Collecte les métadonnées et le classement complet d'une édition (1 requête /players)."""
    tid = tour["id"]
    raw_players = netutil.fetch_json(
        f"{lichess.API_BASE}/broadcast/{tid}/players",
        min_interval=0.7,
        timeout=20,
    )
    if not isinstance(raw_players, list):
        return None
    classement = lichess.normaliser_joueurs(raw_players)
    if len(classement) < 2:
        return None
    # Ignorer les tournois non démarrés (aucune partie jouée et aucun score)
    if not any((j.get("jouees") or 0) > 0 or j.get("score") is not None for j in classement):
        return None

    dates = tour.get("dates") or []
    debut = lichess._ts_to_iso_date(dates[0]) if dates else ""
    fin = lichess._ts_to_iso_date(dates[1]) if len(dates) > 1 else debut
    annee = int(debut[:4]) if len(debut) >= 4 and debut[:4].isdigit() else None
    info = tour.get("info") or {}

    # Déterminer si l'édition est terminée (date de fin passée d'au moins 2 jours)
    today_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    statut = "termine" if (fin and fin < today_iso) else "en_cours"

    podium = [
        {
            "rang": j.get("rang", idx + 1),
            "nom": j["nom"],
            "fide_id": j.get("fide_id", ""),
            "fed": j.get("fed", ""),
            "drapeau": j.get("drapeau", ""),
            "titre": j.get("titre", ""),
            "score": j.get("score"),
            "jouees": j.get("jouees", 0),
        }
        for idx, j in enumerate(classement[:3])
    ]

    return {
        "id": tid,
        "nom": tour.get("name", ""),
        "slug": tour.get("slug", ""),
        "categorie": lichess.categorie_tournoi(tour),
        "format": lichess.detecter_format(tour, len(classement)),
        "tier": tour.get("tier", 0),
        "annee": annee,
        "statut": statut,
        "debut": debut,
        "fin": fin,
        "lieu": info.get("location", ""),
        "cadence": info.get("tc", ""),
        "cadence_fide": info.get("fideTc", ""),
        "url_lichess": tour.get("url", f"https://lichess.org/broadcast/-/{tid}"),
        "nb_joueurs": len(classement),
        "nb_francais": sum(1 for j in classement if j.get("francais")),
        "vainqueur": podium[0] if podium else None,
        "podium": podium,
        "classement": classement,
        "maj": netutil.utc_now_iso(),
    }


def reindexer_historique(
    dossier_historique: str = DOSSIER_HISTORIQUE,
    dossier_tournois: str = "tournois",
) -> dict:
    """Construit historique/index.json à partir de historique/t/*.json."""
    t_dir = os.path.join(dossier_historique, "t")
    os.makedirs(t_dir, exist_ok=True)
    tournois_complets = {
        os.path.splitext(os.path.basename(p))[0]
        for p in glob.glob(os.path.join(dossier_tournois, "*.json"))
        if os.path.basename(p) != "index.json"
    }

    lignes: list[dict] = []
    annees: set[int] = set()
    for path in sorted(glob.glob(os.path.join(t_dir, "*.json"))):
        try:
            with open(path, "r", encoding="utf-8") as fp:
                ed = json.load(fp)
        except Exception:
            continue
        # Vérifier la validité actuelle du périmètre
        if not lichess.est_tournoi_majeur_otb({"name": ed.get("nom", ""), "info": {"location": ed.get("lieu", "")}}):
            continue
        an = ed.get("annee")
        if an:
            annees.add(an)
        lignes.append({
            "id": ed["id"],
            "nom": ed["nom"],
            "categorie": ed.get("categorie", ""),
            "format": ed.get("format", "swiss"),
            "tier": ed.get("tier", 0),
            "annee": an,
            "statut": ed.get("statut", "termine"),
            "debut": ed.get("debut", ""),
            "fin": ed.get("fin", ""),
            "lieu": ed.get("lieu", ""),
            "cadence": ed.get("cadence", ""),
            "nb_joueurs": ed.get("nb_joueurs", len(ed.get("classement") or [])),
            "nb_francais": ed.get("nb_francais", 0),
            "vainqueur": ed.get("vainqueur"),
            "podium": ed.get("podium") or [],
            "a_parties": ed["id"] in tournois_complets,
            "fichier": f"t/{ed['id']}.json",
        })

    lignes.sort(key=lambda x: (x.get("debut") or "", x.get("tier") or 0), reverse=True)
    index = {
        "maj": netutil.utc_now_iso(),
        "nb_tournois": len(lignes),
        "annees": sorted(annees, reverse=True),
        "tournois": lignes,
    }
    netutil.save_json_if_changed(os.path.join(dossier_historique, "index.json"), index)
    return index


def executer_historique(
    dossier_historique: str = DOSSIER_HISTORIQUE,
    dossier_tournois: str = "tournois",
    *,
    annee_min: int = 2021,
    budget: int = 200,
    relire: bool = False,
) -> dict:
    """Collecte (incrémentalement) toutes les éditions majeures du circuit dans historique/."""
    t_dir = os.path.join(dossier_historique, "t")
    os.makedirs(t_dir, exist_ok=True)

    candidats = rechercher_broadcasts_circuit(annee_min=annee_min)
    print(f"[HISTORIQUE] {len(candidats)} éditions majeures repérées ({annee_min}–aujourd'hui).")

    collectes = 0
    ignores = 0
    today_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")

    for tour in candidats:
        if collectes >= budget:
            print(f"[HISTORIQUE] Budget atteint ({budget} éditions) — suite au prochain passage.")
            break
        tid = tour["id"]
        path = os.path.join(t_dir, f"{tid}.json")
        if os.path.exists(path) and not relire:
            try:
                with open(path, "r", encoding="utf-8") as fp:
                    old = json.load(fp)
                if old.get("statut") == "termine" and (old.get("fin") or "") < today_iso and old.get("nb_joueurs", 0) >= 2:
                    ignores += 1
                    continue
            except Exception:
                pass

        try:
            ed = collecter_edition_historique(tour)
        except Exception as e:
            print(f"[WARN] Historique {tid} ({tour.get('name', '')}) : {e}")
            continue
        if not ed:
            continue
        netutil.save_json_if_changed(path, ed)
        collectes += 1
        print(f"  + [{ed['annee']}] [{ed['categorie']}] {ed['nom']} ({ed['nb_joueurs']} j., {ed['nb_francais']} FRA)")

    idx = reindexer_historique(dossier_historique, dossier_tournois)
    print(f"[HISTORIQUE] {idx['nb_tournois']} éditions indexées ({collectes} collectées, {ignores} déjà en cache).")
    return idx


def lister_ids_saison(
    annee: int,
    dossier_historique: str = DOSSIER_HISTORIQUE,
    *,
    max_ids: int = 30,
) -> list[dict]:
    """Retourne les éditions majeures de l'année donnée depuis historique/index.json (triées par tier/date)."""
    idx_path = os.path.join(dossier_historique, "index.json")
    if not os.path.exists(idx_path):
        return []
    with open(idx_path, "r", encoding="utf-8") as fp:
        idx = json.load(fp)
    cands = [t for t in idx.get("tournois", []) if t.get("annee") == annee]
    cands.sort(key=lambda t: (-(t.get("tier") or 0), t.get("debut") or ""), reverse=False)
    # Tri : plus haut tier d'abord, puis plus récents d'abord
    cands.sort(key=lambda t: (t.get("tier") or 0, t.get("debut") or ""), reverse=True)
    return cands[:max_ids]


def _run_tests() -> None:
    assert est_edition_historique_valide({"name": "Tata Steel Chess 2026 | Masters", "tier": 5, "dates": [1768608000000]})
    assert est_edition_historique_valide({"name": "FIDE Candidates 2024 | Open", "tier": 5, "dates": [1712188800000]})
    assert not est_edition_historique_valide({"name": "Tata Steel Masters 2021 - Round 13", "tier": 3, "dates": [1612051200000]})
    assert not est_edition_historique_valide({"name": "GCT: Sinquefield Cup 2025 | Playoff", "tier": 5, "dates": [1756252800000]})
    assert not est_edition_historique_valide({"name": "European Individual Chess Championship 2026 | Boards 67-126", "tier": 5, "dates": [1775520000000]})
    assert not est_edition_historique_valide({"name": "2026 Titled Tuesday Online", "tier": 5, "dates": [1768608000000]})
    print("[OK] historique.py : tous les tests passent.")


if __name__ == "__main__":
    if "--test" in sys.argv:
        _run_tests()
    else:
        print("Usage: python3 historique.py --test")
