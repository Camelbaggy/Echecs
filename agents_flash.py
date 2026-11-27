#!/usr/bin/env python3
"""Résumés factuels des parties d'échecs (Gemini Flash rédacteur + Flash-Lite contrôleur).

Même discipline que coupes-europe :
- Le modèle reçoit UNIQUEMENT un dossier factuel déterministe (ouverture, coups clés,
  précision, crise de temps, enjeu au classement) et a interdiction d'inventer des variantes.
- Sans GEMINI_API_KEY, si AI_BUDGET_SECONDS=0, ou après 3 rejets du contrôleur :
  repli déterministe propre en français.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

import netutil

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


def _nom_court(nom: str) -> str:
    if "," in nom:
        parts = [s.strip() for s in nom.split(",", 1)]
        return f"{parts[1]} {parts[0]}".strip()
    return nom


def build_game_dossier(partie: dict, tournoi: dict) -> dict:
    """Construit le dossier factuel strict transmis au rédacteur et au contrôleur."""
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


def _discover_models(api_key: str) -> tuple[str | None, str | None]:
    """Découvre via ListModels un modèle Flash (rédacteur) et Flash-Lite (contrôleur)."""
    url = f"{GEMINI_BASE}/models?key={api_key}"
    try:
        data = netutil.fetch_json(url, timeout=15, min_interval=0.2)
    except Exception:
        return None, None
    models = data.get("models", []) if isinstance(data, dict) else []
    gen = [
        m["name"] for m in models
        if "generateContent" in (m.get("supportedGenerationMethods") or [])
    ]
    writer = next((m for m in gen if "flash" in m and "lite" not in m and "thinking" not in m), None)
    checker = next((m for m in gen if "flash-lite" in m), writer)
    return writer, checker


def _call_gemini(api_key: str, model: str, prompt: str, temperature: float = 0.25) -> str:
    url = f"{GEMINI_BASE}/{model}:generateContent?key={api_key}"
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 450},
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json", "User-Agent": netutil.UA}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    cands = body.get("candidates") or []
    parts = (cands[0].get("content") or {}).get("parts") or [] if cands else []
    return "".join(p.get("text", "") for p in parts).strip()


def resumer_partie_gemini(
    partie: dict,
    tournoi: dict,
    *,
    api_key: str,
    writer: str,
    checker: str,
    deadline: float,
) -> tuple[str, str]:
    """Génère un résumé Gemini vérifié ; sinon repli déterministe."""
    dossier = build_game_dossier(partie, tournoi)
    dossier_json = json.dumps(dossier, ensure_ascii=False)
    retour_controle = ""

    for essai in range(1, 4):
        if time.monotonic() >= deadline:
            break
        prompt_w = (
            "Tu es journaliste d'échecs francophone. Rédige un résumé factuel de 3 à 4 phrases "
            "en français de cette partie à partir UNIQUEMENT du dossier JSON ci-dessous.\n"
            "Règles absolues : n'invente AUCUN coup, AUCUNE variante, AUCUN chiffre absent du dossier. "
            "Mentionne le résultat, l'ouverture si présente, le tournant objectif s'il existe, et la précision si présente.\n"
            f"{('Correction demandée par le contrôleur : ' + retour_controle + chr(10)) if retour_controle else ''}"
            f"Dossier : {dossier_json}"
        )
        try:
            texte = _call_gemini(api_key, writer, prompt_w, temperature=0.25)
            if not texte:
                continue
            if time.monotonic() >= deadline:
                break
            prompt_c = (
                "Tu es contrôleur factuel strict. Vérifie si chaque nom, résultat, ouverture, numéro de coup, "
                "coup SAN et chiffre du résumé ci-dessous est STRICTEMENT présent dans le dossier JSON.\n"
                "Réponds uniquement par 'OK' si tout est exact, sinon par 'REJET: <raison brève>'.\n"
                f"Dossier : {dossier_json}\nRésumé : {texte}"
            )
            verdict = _call_gemini(api_key, checker, prompt_c, temperature=0.0)
            if verdict.upper().startswith("OK"):
                return texte, "gemini"
            retour_controle = verdict
            print(f"[IA] Rejet essai {essai}/3 ({partie['id']}) : {verdict[:120]}")
        except urllib.error.HTTPError as e:
            print(f"[IA] HTTP {e.code} ({partie['id']})")
            if e.code == 429:
                break
        except Exception as e:
            print(f"[IA] Erreur ({partie['id']}) : {e}")
            break

    return build_deterministic_summary(partie, tournoi), "deterministe"


def enrichir_resumes_tournoi(tournoi: dict, *, max_gemini: int = 12) -> int:
    """Ajoute un résumé (Gemini ou déterministe) aux parties terminées de Français et affiches majeures."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    budget_s = int(os.environ.get("AI_BUDGET_SECONDS", "600"))
    use_ai = bool(api_key) and budget_s > 0
    writer = checker = None
    if use_ai:
        writer, checker = _discover_models(api_key)
        if not writer or not checker:
            use_ai = False

    deadline = time.monotonic() + max(0, budget_s)
    gemini_used = 0
    total_done = 0

    for ronde in reversed(tournoi.get("rondes", [])):
        for p in ronde.get("parties", []):
            if p.get("statut") != "terminee":
                continue
            cible = p.get("est_francais") or (p.get("echiquier", 99) <= 2 and p.get("resultat") != "1/2-1/2")
            if not cible:
                continue
            if p.get("resume") and p.get("resume_source") == "gemini":
                continue

            if use_ai and gemini_used < max_gemini and time.monotonic() < deadline:
                assert writer and checker
                texte, src = resumer_partie_gemini(
                    p, tournoi, api_key=api_key, writer=writer, checker=checker, deadline=deadline
                )
                if src == "gemini":
                    gemini_used += 1
            else:
                texte, src = build_deterministic_summary(p, tournoi), "deterministe"

            p["resume"] = texte
            p["resume_source"] = src
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
