#!/usr/bin/env python3
"""Résumés factuels déterministes des parties d'échecs (sans appel IA).

Produit un résumé en français de 2 à 4 phrases uniquement à partir des faits
vérifiés de la partie (résultat, ouverture, coup tournant objectif, précision,
pression à la pendule). Zéro hallucination.
"""

from __future__ import annotations

import sys

import netutil


def _nom_court(nom: str) -> str:
    if "," in nom:
        parts = [s.strip() for s in nom.split(",", 1)]
        return f"{parts[1]} {parts[0]}".strip()
    return nom


def build_game_dossier(partie: dict, tournoi: dict) -> dict:
    """Construit le dossier factuel strict d'une partie."""
    b = partie["blancs"]
    n = partie["noirs"]
    ana = partie.get("analyse") or {}

    rangs = {j.get("fide_id") or j.get("nom"): j for j in tournoi.get("classement", [])}
    rb = rangs.get(b.get("fide_id") or b.get("nom"))
    rn = rangs.get(n.get("fide_id") or n.get("nom"))

    dossier: dict = {
        "tournoi": tournoi.get("nom", ""),
        "ronde": partie.get("ronde", ""),
        "date": netutil.date_fr(partie.get("date", "")),
        "blancs": {
            "nom": _nom_court(b["nom"]),
            "titre": b.get("titre", ""),
            "elo": b.get("elo"),
            "fed": b.get("fed", ""),
            "rang": rb.get("rang") if rb else None,
            "score_tournoi": rb.get("score") if rb else None,
        },
        "noirs": {
            "nom": _nom_court(n["nom"]),
            "titre": n.get("titre", ""),
            "elo": n.get("elo"),
            "fed": n.get("fed", ""),
            "rang": rn.get("rang") if rn else None,
            "score_tournoi": rn.get("score") if rn else None,
        },
        "resultat": partie.get("resultat", "*"),
        "nb_coups": partie.get("nb_coups", 0),
        "eco": partie.get("eco", ""),
        "ouverture": partie.get("ouverture", ""),
    }
    if ana.get("blancs") and ana.get("noirs"):
        dossier["precision"] = {
            "blancs": ana["blancs"].get("precision"),
            "noirs": ana["noirs"].get("precision"),
            "gaffes_blancs": ana["blancs"].get("gaffes", 0),
            "gaffes_noirs": ana["noirs"].get("gaffes", 0),
        }
        dossier["tournants"] = ana.get("tournants", [])[:3]
    if ana.get("temps"):
        dossier["zeitnot"] = ana["temps"].get("zeitnot")
    return dossier


def build_deterministic_summary(partie: dict, tournoi: dict) -> str:
    """Construit un résumé narratif factuel en français sans appel IA."""
    d = build_game_dossier(partie, tournoi)
    bn = d["blancs"]["nom"]
    nn = d["noirs"]["nom"]
    res = d["resultat"]
    nb = d["nb_coups"]
    ouv = d["ouverture"] or d["eco"]

    if res == "1-0":
        p1 = f"{bn} s'impose avec les Blancs face à {nn} en {nb} coups"
    elif res == "0-1":
        p1 = f"{nn} l'emporte avec les Noirs contre {bn} en {nb} coups"
    elif res == "1/2-1/2":
        p1 = f"{bn} et {nn} partagent le point après {nb} coups"
    else:
        p1 = f"Partie entre {bn} et {nn} ({nb} coups)"
    if ouv:
        p1 += f" dans une {ouv}"
    p1 += "."

    phrases = [p1]

    tournants = d.get("tournants") or []
    if tournants:
        t = tournants[0]
        camp = bn if t["camp"] == "blancs" else nn
        label = {"gaffe": "une gaffe", "erreur": "une erreur", "imprecision": "une imprécision"}.get(
            t["type"], "un tournant"
        )
        phrases.append(
            f"Le tournant intervient au {t['coup']}e coup avec {label} de {camp} ({t['san']}, évaluation {t['eval']})."
        )

    prec = d.get("precision")
    if prec and prec.get("blancs") is not None and prec.get("noirs") is not None:
        pb = f"{prec['blancs']:.1f}".replace(".", ",")
        pn = f"{prec['noirs']:.1f}".replace(".", ",")
        phrases.append(f"Côté précision, {bn} termine à {pb} % contre {pn} % pour {nn}.")

    z = d.get("zeitnot") or {}
    if z.get("blancs") and z.get("noirs"):
        phrases.append("Les deux joueurs ont dû gérer une crise de temps en fin de partie.")
    elif z.get("blancs"):
        phrases.append(f"{bn} s'est retrouvé sous pression à la pendule.")
    elif z.get("noirs"):
        phrases.append(f"{nn} s'est retrouvé sous pression à la pendule.")

    return " ".join(phrases)


def enrichir_resumes_tournoi(tournoi: dict) -> int:
    """Ajoute un résumé déterministe aux parties terminées de Français et affiches majeures."""
    total_done = 0
    for ronde in reversed(tournoi.get("rondes", [])):
        for p in ronde.get("parties", []):
            if p.get("statut") != "terminee":
                continue
            cible = p.get("est_francais") or (p.get("echiquier", 99) <= 2 and p.get("resultat") != "1/2-1/2")
            if not cible:
                continue
            p["resume"] = build_deterministic_summary(p, tournoi)
            p["resume_source"] = "deterministe"
            total_done += 1
    return total_done


def _run_tests() -> None:
    tournoi = {
        "nom": "Tata Steel Masters 2026",
        "classement": [
            {"fide_id": "12573981", "nom": "Firouzja, Alireza", "rang": 2, "score": 7.5},
            {"fide_id": "1503014", "nom": "Carlsen, Magnus", "rang": 1, "score": 8.0},
        ],
    }
    partie = {
        "id": "tata-r9-1",
        "echiquier": 1,
        "ronde": "Ronde 9",
        "date": "2026-01-26",
        "blancs": {"nom": "Firouzja, Alireza", "titre": "GM", "elo": 2763, "fed": "FRA", "fide_id": "12573981"},
        "noirs": {"nom": "Carlsen, Magnus", "titre": "GM", "elo": 2832, "fed": "NOR", "fide_id": "1503014"},
        "resultat": "1-0",
        "statut": "terminee",
        "est_francais": True,
        "nb_coups": 41,
        "eco": "C65",
        "ouverture": "Défense berlinoise",
        "analyse": {
            "blancs": {"precision": 94.2, "gaffes": 0},
            "noirs": {"precision": 86.5, "gaffes": 1},
            "tournants": [{"coup": 31, "camp": "noirs", "san": "Rd7", "type": "gaffe", "eval": "+3,40"}],
            "temps": {"zeitnot": {"blancs": False, "noirs": True}},
        },
    }
    txt = build_deterministic_summary(partie, tournoi)
    assert "Alireza Firouzja" in txt and "41 coups" in txt and "94,2 %" in txt and "Rd7" in txt
    print("[OK] agents_flash.py : tous les tests passent.")


if __name__ == "__main__":
    if "--test" in sys.argv:
        _run_tests()
    else:
        print("Usage: python3 agents_flash.py --test")
