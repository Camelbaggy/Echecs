#!/usr/bin/env python3
"""Collecteur Lichess Broadcasts API pour les tournois OTB majeurs (Python 3.11 stdlib).

Source officielle, publique, sans clé en lecture :
- GET /api/broadcast/top?page=N
- GET /api/broadcast/{tourId}
- GET /broadcast/{tourId}/players
- GET /api/broadcast/round/{roundId}.pgn
Respecte strictement la règle Lichess : 1 requête séquentielle à la fois, pause 60s sur HTTP 429.
"""

from __future__ import annotations

import datetime as dt
import re
import sys

import analyse
import netutil
import pgn

API_BASE = "https://lichess.org"
MIN_TIER = 4

_MOTS_FIDE = (
    "world championship", "championnat du monde", "candidates", "candidats",
    "world cup", "coupe du monde", "grand swiss", "olympiad", "olympiade",
    "world rapid", "world blitz", "fide grand prix", "fide circuit",
    "women's world", "women's candidates", "world team",
)
_MOTS_SUPER = (
    "tata steel", "wijk aan zee", "norway chess", "sinquefield", "grand chess tour",
    "superbet", "prague international", "grenke", "freestyle chess", "biel",
    "dortmund", "london chess classic", "st. louis", "saint louis", "american cup",
    "cairns cup", "uzchess", "chennai grand masters", "wr chess", "sharjah masters",
    "qatar masters", "reykjavik open", "aeroflot", "tepe sigeman",
)
_MOTS_FRANCE = (
    "championnat de france", "french championship", "top 16", "top16",
    "european individual", "european championship", "championnat d'europe",
    "european team", "european club cup", "mitropa", "cappelle", "chartres",
)
_MOTS_EN_LIGNE = (
    "titled tuesday", "titled arena", "lichess arena", "bullet brawl",
    "speed chess championship", "champions chess tour", "pro chess league",
    "chess.com", "online",
)


def categorie_tournoi(tour: dict) -> str:
    """Classe un tournoi en catégorie pour le menu latéral : fide | super | france | autres."""
    nom = netutil.normalize(tour.get("name", ""))
    if any(m in nom for m in _MOTS_FIDE):
        return "fide"
    if any(m in nom for m in _MOTS_SUPER):
        return "super"
    if any(m in nom for m in _MOTS_FRANCE):
        return "france"
    return "autres"


def est_tournoi_majeur_otb(tour: dict) -> bool:
    """Sélectionne les tournois OTB majeurs (tier >= 4 ou circuit majeur, hors tournois en ligne)."""
    nom = netutil.normalize(tour.get("name", ""))
    info = tour.get("info") or {}
    loc = netutil.normalize(info.get("location", ""))

    if any(m in nom for m in _MOTS_EN_LIGNE) or loc in ("online", "internet"):
        return False

    tier = int(tour.get("tier") or 0)
    if tier >= MIN_TIER:
        return True
    return categorie_tournoi(tour) in ("fide", "super", "france")


def detecter_format(tour: dict, nb_joueurs: int = 0) -> str:
    """Détecte le format du tournoi : round_robin | swiss | knockout | match."""
    info = tour.get("info") or {}
    fmt = netutil.normalize(info.get("format", ""))
    nom = netutil.normalize(tour.get("name", ""))
    if "knockout" in fmt or "ko" in fmt.split() or "world cup" in nom:
        return "knockout"
    if "match" in fmt or ("world championship" in nom and "rapid" not in nom and "blitz" not in nom and nb_joueurs <= 2):
        return "match"
    if "swiss" in fmt or "suisse" in fmt or nb_joueurs >= 20:
        return "swiss"
    if "round robin" in fmt or "round-robin" in fmt or "toutes rondes" in fmt or (2 < nb_joueurs <= 18):
        return "round_robin"
    return "swiss" if nb_joueurs > 16 else "round_robin"


def _ts_to_iso_date(ms: int | None) -> str:
    if not ms:
        return ""
    return dt.datetime.fromtimestamp(ms / 1000.0, tz=dt.timezone.utc).strftime("%Y-%m-%d")


def _ts_to_iso(ms: int | None) -> str:
    if not ms:
        return ""
    return dt.datetime.fromtimestamp(ms / 1000.0, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normaliser_joueurs(raw_players: list[dict]) -> list[dict]:
    """Normalise la réponse de /broadcast/{tourId}/players en classement trié."""
    joueurs: list[dict] = []
    for p in raw_players:
        fed = (p.get("fed") or "").strip().upper()
        fide_id = str(p.get("fideId") or "").strip()
        nom = (p.get("name") or "").strip()
        if not nom:
            continue
        rec: dict = {
            "nom": nom,
            "fide_id": fide_id,
            "fed": fed,
            "drapeau": netutil.fide_flag(fed),
            "pays": netutil.fide_pays(fed),
            "francais": fed == "FRA",
            "titre": (p.get("title") or "").strip(),
            "elo": p.get("rating"),
            "score": p.get("score"),
            "jouees": p.get("played", 0),
            "perf": p.get("performance"),
            "diff_elo": p.get("ratingDiff"),
        }
        if p.get("rank") is not None:
            rec["rang"] = p["rank"]
        if p.get("tiebreaks"):
            rec["departages"] = p["tiebreaks"]
        joueurs.append(rec)

    # Trier par rang Lichess si présent, sinon par score décroissant puis Elo
    joueurs.sort(
        key=lambda j: (
            j.get("rang") if j.get("rang") is not None else 9999,
            -(j["score"] if j["score"] is not None else -1.0),
            -(j["elo"] or 0),
            j["nom"],
        )
    )
    for idx, j in enumerate(joueurs, 1):
        if "rang" not in j:
            j["rang"] = idx
    return joueurs


def _index_joueurs(joueurs: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    by_fide: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for j in joueurs:
        if j.get("fide_id"):
            by_fide[j["fide_id"]] = j
        by_name[netutil.normalize(j["nom"])] = j
        # Aussi forme "Prénom Nom" si "Nom, Prénom"
        if "," in j["nom"]:
            parts = [s.strip() for s in j["nom"].split(",", 1)]
            by_name[netutil.normalize(f"{parts[1]} {parts[0]}")] = j
    return by_fide, by_name


def _resoudre_joueur(
    nom_pgn: str,
    fide_id_pgn: str,
    elo_pgn: str,
    titre_pgn: str,
    by_fide: dict[str, dict],
    by_name: dict[str, dict],
) -> dict:
    ref = None
    if fide_id_pgn and fide_id_pgn in by_fide:
        ref = by_fide[fide_id_pgn]
    else:
        key = netutil.normalize(nom_pgn)
        if key in by_name:
            ref = by_name[key]
        elif "," in nom_pgn:
            p = [s.strip() for s in nom_pgn.split(",", 1)]
            ref = by_name.get(netutil.normalize(f"{p[1]} {p[0]}"))

    elo_val = int(elo_pgn) if elo_pgn and elo_pgn.isdigit() else (ref["elo"] if ref else None)
    fed = ref["fed"] if ref else ""
    return {
        "nom": ref["nom"] if ref else nom_pgn,
        "fide_id": fide_id_pgn or (ref["fide_id"] if ref else ""),
        "fed": fed,
        "drapeau": netutil.fide_flag(fed),
        "francais": fed == "FRA",
        "titre": titre_pgn or (ref["titre"] if ref else ""),
        "elo": elo_val,
    }


def _statut_partie(resultat: str) -> str:
    if resultat in ("1-0", "0-1", "1/2-1/2"):
        return "terminee"
    return "en_cours"


def construire_parties_ronde(
    pgn_text: str,
    tour_id: str,
    ronde_id: str,
    ronde_nom: str,
    joueurs: list[dict],
    sf_runner: analyse.StockfishRunner | None = None,
    sf_budget_parties: list[int] | None = None,
) -> list[dict]:
    """Parse le PGN d'une ronde et construit les fiches de partie complètes."""
    by_fide, by_name = _index_joueurs(joueurs)
    parsed = pgn.parse_pgn_games(pgn_text)
    parties: list[dict] = []

    for idx, g in enumerate(parsed, 1):
        h = g["headers"]
        plies = g["plies"]
        blancs = _resoudre_joueur(
            h.get("White", "?"),
            h.get("WhiteFideId", ""),
            h.get("WhiteElo", ""),
            h.get("WhiteTitle", ""),
            by_fide,
            by_name,
        )
        noirs = _resoudre_joueur(
            h.get("Black", "?"),
            h.get("BlackFideId", ""),
            h.get("BlackElo", ""),
            h.get("BlackTitle", ""),
            by_fide,
            by_name,
        )
        resultat = h.get("Result", "*").strip()
        statut = _statut_partie(resultat)
        est_fra = bool(blancs["francais"] or noirs["francais"])

        eval_source = ""
        has_pgn_eval = sum(1 for p in plies if "eval" in p or "mate" in p) >= max(4, len(plies) // 2)
        if has_pgn_eval:
            eval_source = "lichess"
        elif (
            statut == "terminee"
            and g["uci_complet"]
            and sf_runner is not None
            and sf_runner.available
            and (sf_budget_parties is None or sf_budget_parties[0] > 0)
        ):
            # Priorité aux parties de Français et aux 10 premiers échiquiers
            if est_fra or idx <= 10:
                ok = sf_runner.evaluer_partie(plies)
                if ok:
                    eval_source = "stockfish"
                    if sf_budget_parties is not None:
                        sf_budget_parties[0] -= 1

        tc = h.get("TimeControl", "")
        ana = analyse.analyser_partie(plies, tc)

        date_str = h.get("UTCDate") or h.get("Date") or ""
        date_iso = date_str.replace(".", "-") if re.match(r"^\d{4}\.\d{2}\.\d{2}$", date_str) else ""

        # Stocker les coups sous forme compacte pour limiter la taille JSON
        coups_compacts = []
        for p in plies:
            item: dict = {"s": p["san"]}
            if "uci" in p:
                item["u"] = p["uci"]
            if "eval" in p:
                item["e"] = p["eval"]
            if "mate" in p:
                item["m"] = p["mate"]
            if "clk" in p:
                item["c"] = p["clk"]
            if "classe" in p:
                item["k"] = p["classe"]
            coups_compacts.append(item)

        partie: dict = {
            "id": f"{tour_id}-{ronde_id}-{idx}",
            "echiquier": idx,
            "ronde_id": ronde_id,
            "ronde": ronde_nom,
            "date": date_iso,
            "blancs": blancs,
            "noirs": noirs,
            "resultat": resultat,
            "statut": statut,
            "est_francais": est_fra,
            "nb_coups": (len(plies) + 1) // 2,
            "eco": h.get("ECO", ""),
            "ouverture": h.get("Opening", ""),
            "cadence": tc,
            "terminaison": h.get("Termination", ""),
            "coups": coups_compacts,
        }
        if h.get("GameURL") or h.get("Site", "").startswith("https://lichess.org/"):
            partie["url_lichess"] = h.get("GameURL") or h.get("Site")
        if eval_source:
            partie["eval_source"] = eval_source
        if ana:
            partie["analyse"] = ana

        parties.append(partie)

    return parties


def fetch_top_broadcasts(pages: int = 2) -> list[dict]:
    """Récupère les tournois actifs, à venir et récents depuis /api/broadcast/top."""
    seen: set[str] = set()
    result: list[dict] = []
    for page in range(1, pages + 1):
        data = netutil.fetch_json(f"{API_BASE}/api/broadcast/top?page={page}", min_interval=0.6)
        if not isinstance(data, dict):
            continue
        sections = []
        if page == 1:
            sections.extend(("actif", item) for item in data.get("active", []))
            sections.extend(("a_venir", item) for item in data.get("upcoming", []))
        past = data.get("past", {})
        past_list = past.get("currentPageResults", []) if isinstance(past, dict) else []
        sections.extend(("passe", item) for item in past_list)

        for etat, item in sections:
            tour = item.get("tour") or {}
            tid = tour.get("id")
            if not tid or tid in seen:
                continue
            seen.add(tid)
            if est_tournoi_majeur_otb(tour):
                result.append({"etat": etat, "tour": tour, "round": item.get("round")})
    return result


def collecter_tournoi(
    tour_id: str,
    *,
    sf_runner: analyse.StockfishRunner | None = None,
    sf_budget_parties: list[int] | None = None,
    max_rondes_pgn: int = 15,
) -> dict:
    """Collecte un tournoi complet (métadonnées, joueurs/classement, rondes et parties)."""
    tdata = netutil.fetch_json(f"{API_BASE}/api/broadcast/{tour_id}", min_interval=0.6)
    assert isinstance(tdata, dict)
    tour = tdata.get("tour") or {}
    rounds_raw = tdata.get("rounds") or []
    info = tour.get("info") or {}

    try:
        players_raw = netutil.fetch_json(f"{API_BASE}/broadcast/{tour_id}/players", min_interval=0.6)
        joueurs = normaliser_joueurs(players_raw if isinstance(players_raw, list) else [])
    except Exception as e:
        print(f"[WARN] Joueurs illisibles pour {tour_id} : {e}")
        joueurs = []

    dates = tour.get("dates") or []
    debut = _ts_to_iso_date(dates[0]) if len(dates) >= 1 else ""
    fin = _ts_to_iso_date(dates[1]) if len(dates) >= 2 else ""

    rondes: list[dict] = []
    # Collecter les PGN des rondes terminées ou en cours (les plus récentes en priorité)
    rondes_a_lire = [r for r in rounds_raw if r.get("finished") or r.get("ongoing")]
    rondes_a_lire = rondes_a_lire[-max_rondes_pgn:]
    ids_a_lire = {r["id"] for r in rondes_a_lire}

    for r in rounds_raw:
        rid = r["id"]
        rnom = r.get("name") or f"Ronde {len(rondes) + 1}"
        statut_r = "terminee" if r.get("finished") else ("en_cours" if r.get("ongoing") else "a_venir")
        ronde_obj: dict = {
            "id": rid,
            "nom": rnom,
            "statut": statut_r,
            "debut": _ts_to_iso(r.get("startsAt")),
            "parties": [],
        }
        if rid in ids_a_lire:
            try:
                pgn_text = netutil.fetch_text(f"{API_BASE}/api/broadcast/round/{rid}.pgn", min_interval=0.6)
                ronde_obj["parties"] = construire_parties_ronde(
                    pgn_text,
                    tour_id,
                    rid,
                    rnom,
                    joueurs,
                    sf_runner=sf_runner,
                    sf_budget_parties=sf_budget_parties,
                )
            except Exception as e:
                print(f"[WARN] PGN illisible pour {tour_id}/{rid} : {e}")
        rondes.append(ronde_obj)

    nb_fra = sum(1 for j in joueurs if j.get("francais"))
    statut_t = (
        "en_cours" if any(r["statut"] == "en_cours" for r in rondes)
        else ("termine" if rondes and all(r["statut"] == "terminee" for r in rondes)
              else ("a_venir" if rondes and all(r["statut"] == "a_venir" for r in rondes) else "en_cours"))
    )

    return {
        "id": tour_id,
        "nom": tour.get("name", tour_id),
        "slug": tour.get("slug", netutil.slugify(tour.get("name", tour_id))),
        "categorie": categorie_tournoi(tour),
        "format": detecter_format(tour, len(joueurs)),
        "tier": int(tour.get("tier") or 0),
        "statut": statut_t,
        "debut": debut,
        "fin": fin,
        "lieu": info.get("location", ""),
        "cadence": info.get("tc", ""),
        "cadence_fide": info.get("fideTc", ""),
        "site": info.get("website", ""),
        "url_lichess": tour.get("url", f"{API_BASE}/broadcast/-/{tour_id}"),
        "source": "Lichess",
        "nb_francais": nb_fra,
        "classement": joueurs,
        "rondes": rondes,
        "maj": netutil.utc_now_iso(),
    }


def _run_tests() -> None:
    assert est_tournoi_majeur_otb({"name": "Tata Steel Masters 2026", "tier": 5, "info": {"location": "Wijk aan Zee"}})
    assert est_tournoi_majeur_otb({"name": "Championnat de France 2026", "tier": 3, "info": {"location": "Vichy"}})
    assert not est_tournoi_majeur_otb({"name": "Titled Tuesday Blitz", "tier": 5, "info": {"location": "Online"}})
    assert categorie_tournoi({"name": "FIDE World Championship 2026"}) == "fide"
    assert categorie_tournoi({"name": "Norway Chess 2026"}) == "super"
    assert categorie_tournoi({"name": "Championnat de France 2026"}) == "france"

    joueurs = normaliser_joueurs([
        {"name": "Firouzja, Alireza", "fideId": 12573981, "fed": "FRA", "title": "GM", "rating": 2763, "score": 6.5, "played": 9},
        {"name": "Carlsen, Magnus", "fideId": 1503014, "fed": "NOR", "title": "GM", "rating": 2832, "score": 7.0, "played": 9},
    ])
    assert joueurs[0]["nom"] == "Carlsen, Magnus" and joueurs[0]["rang"] == 1
    assert joueurs[1]["francais"] is True and joueurs[1]["drapeau"] == "fr"

    sample_pgn = """
[Event "Norway Chess 2026"]
[White "Firouzja, Alireza"]
[Black "Carlsen, Magnus"]
[Result "1/2-1/2"]
[WhiteFideId "12573981"]
[BlackFideId "1503014"]
[ECO "C65"]
[Opening "Ruy Lopez: Berlin Defense"]

1. e4 { [%eval 0.20] [%clk 2:00:00] } 1... e5 { [%eval 0.20] [%clk 2:00:00] }
2. Nf3 { [%eval 0.22] [%clk 1:59:45] } 2... Nc6 { [%eval 0.20] [%clk 1:59:50] }
3. Bb5 { [%eval 0.18] [%clk 1:59:30] } 3... Nf6 { [%eval 0.20] [%clk 1:59:40] } 1/2-1/2
"""
    parties = construire_parties_ronde(sample_pgn, "nor26", "r1", "Ronde 1", joueurs)
    assert len(parties) == 1
    assert parties[0]["est_francais"] is True
    assert parties[0]["eval_source"] == "lichess"
    assert parties[0]["analyse"]["blancs"]["precision"] > 95.0

    print("[OK] lichess.py : tous les tests passent.")


if __name__ == "__main__":
    if "--test" in sys.argv:
        _run_tests()
    else:
        print("Usage: python3 lichess.py --test")
