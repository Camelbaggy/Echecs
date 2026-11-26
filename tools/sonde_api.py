#!/usr/bin/env python3
"""Sonde API réelle (étape 0) pour le dashboard Échecs.

Mesure en conditions réelles (GitHub Actions, Python 3.11 stdlib) :
1. Lichess Broadcasts API (liste top, métadonnées tournoi, joueurs/classement, PGN, CORS, [%eval], [%clk]).
2. FIDE ratings.fide.com (URLs de téléchargement, temps de téléchargement/parsing, Top 10 monde & France).
3. Stockfish (version apt, temps d'analyse d'une partie réelle à profondeur 16 via UCI).
4. Wikidata (P1440) + Wikipédia FR (lot MediaWiki) sur 5 grands maîtres français.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

UA = "EchecsDashboardSonde/0.1 (https://github.com/Camelbaggy/Echecs)"


def fetch(url: str, accept: str | None = None, timeout: int = 30) -> tuple[int, dict[str, str], bytes]:
    headers = {"User-Agent": UA}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


def sonde_lichess() -> str | None:
    print("=" * 72)
    print("1. LICHESS BROADCASTS API")
    print("=" * 72)
    t0 = time.monotonic()
    status, headers, body = fetch("https://lichess.org/api/broadcast/top?page=1")
    dt = time.monotonic() - t0
    print(f"GET /api/broadcast/top?page=1 -> HTTP {status} ({len(body)} octets, {dt:.2f}s)")
    print(f"  CORS: {headers.get('access-control-allow-origin')} | CT: {headers.get('content-type')}")
    if status != 200:
        print("  ERREUR:", body[:300])
        return None

    data = json.loads(body.decode("utf-8"))
    print("  Clés racine:", list(data.keys()))

    active = data.get("active", [])
    upcoming = data.get("upcoming", [])
    past_obj = data.get("past", {})
    past = past_obj.get("currentPageResults", []) if isinstance(past_obj, dict) else past_obj
    print(f"  Actifs: {len(active)} | À venir: {len(upcoming)} | Passés (p.1): {len(past)}")

    # Sélectionner jusqu'à 6 tournois de plus haut tier (actifs puis passés)
    candidats = []
    for src_label, lst in (("actif", active), ("passe", past)):
        for item in lst:
            tour = item.get("tour", {})
            candidats.append((tour.get("tier", 0), src_label, item))
    candidats.sort(key=lambda x: x[0], reverse=True)

    print("\n  Aperçu des 10 premiers tournois par tier :")
    for tier, src, item in candidats[:10]:
        t = item.get("tour", {})
        info = t.get("info", {})
        r = item.get("round", {})
        print(
            f"    [{src:5s}] tier={tier} id={t.get('id')} | {t.get('name')} | "
            f"tc={info.get('tc')} fideTc={info.get('fideTc')} format={info.get('format')} | "
            f"ronde={r.get('name')} finished={r.get('finished')} ongoing={r.get('ongoing')}"
        )

    sample_pgn_for_sf: str | None = None
    tested = 0
    seen_ids: set[str] = set()
    print("\n  Inspection détaillée de 5 tournois majeurs :")
    for tier, src, item in candidats:
        if tested >= 5:
            break
        t = item.get("tour", {})
        tid = t.get("id")
        if not tid or tid in seen_ids:
            continue
        seen_ids.add(tid)
        tested += 1
        time.sleep(0.5)

        st_t, h_t, b_t = fetch(f"https://lichess.org/api/broadcast/{tid}")
        print(f"\n  --- Tournoi {tid} ({t.get('name')}, tier={tier}) ---")
        print(f"    GET /api/broadcast/{tid} -> HTTP {st_t}, CORS={h_t.get('access-control-allow-origin')}, {len(b_t)} o")
        if st_t != 200:
            continue
        tdata = json.loads(b_t.decode("utf-8"))
        rounds = tdata.get("rounds", [])
        finished_rounds = [r for r in rounds if r.get("finished")]
        ongoing_rounds = [r for r in rounds if r.get("ongoing")]
        print(f"    Rondes: total={len(rounds)}, finies={len(finished_rounds)}, en_cours={len(ongoing_rounds)}")
        if rounds:
            print(f"    Clés d'une ronde: {list(rounds[0].keys())}")

        time.sleep(0.5)
        st_p, h_p, b_p = fetch(f"https://lichess.org/broadcast/{tid}/players")
        print(f"    GET /broadcast/{tid}/players -> HTTP {st_p}, CORS={h_p.get('access-control-allow-origin')}, {len(b_p)} o")
        if st_p == 200:
            players = json.loads(b_p.decode("utf-8"))
            with_fide = sum(1 for p in players if p.get("fideId"))
            with_fed = sum(1 for p in players if p.get("fed"))
            fra = [p for p in players if p.get("fed") == "FRA"]
            print(f"    Joueurs: {len(players)} | avec fideId: {with_fide} | avec fed: {with_fed} | FRA: {len(fra)}")
            if players:
                print(f"    Clés joueur: {list(players[0].keys())}")
                print(f"    Ex. joueur #1: {json.dumps(players[0], ensure_ascii=False)[:220]}")
            if fra:
                print(f"    Ex. Français: {[p.get('name') for p in fra[:5]]}")

        target_round = finished_rounds[-1] if finished_rounds else (ongoing_rounds[0] if ongoing_rounds else None)
        if target_round:
            rid = target_round["id"]
            time.sleep(0.5)
            st_g, h_g, b_g = fetch(f"https://lichess.org/api/broadcast/round/{rid}.pgn")
            print(f"    GET /api/broadcast/round/{rid}.pgn -> HTTP {st_g}, CORS={h_g.get('access-control-allow-origin')}, {len(b_g)} o")
            if st_g == 200:
                pgn_text = b_g.decode("utf-8", errors="replace")
                games = [g.strip() for g in re.split(r"\n\n(?=\[Event )", pgn_text.strip()) if g.strip()]
                n_eval = sum(1 for g in games if "[%eval " in g)
                n_clk = sum(1 for g in games if "[%clk " in g)
                n_fide_tag = sum(1 for g in games if "[WhiteFideId " in g)
                n_eco = sum(1 for g in games if "[ECO " in g)
                print(
                    f"    PGN ronde {target_round.get('name')}: {len(games)} parties | "
                    f"[%eval]: {n_eval}/{len(games)} | [%clk]: {n_clk}/{len(games)} | "
                    f"WhiteFideId: {n_fide_tag}/{len(games)} | ECO: {n_eco}/{len(games)}"
                )
                if games and sample_pgn_for_sf is None:
                    sample_pgn_for_sf = games[0]
                    print("    Extrait PGN partie #1 (400 car.):")
                    print("      " + games[0][:400].replace("\n", "\n      "))

    return sample_pgn_for_sf


def sonde_fide() -> None:
    print("\n" + "=" * 72)
    print("2. FIDE RATINGS (ratings.fide.com)")
    print("=" * 72)
    st, _, body = fetch("https://ratings.fide.com/download_lists.phtml")
    print(f"GET download_lists.phtml -> HTTP {st} ({len(body)} o)")
    if st != 200:
        return
    html = body.decode("utf-8", errors="replace")
    zips = sorted(set(re.findall(r'https?://ratings\.fide\.com/download/[A-Za-z0-9_.-]+\.zip|/download/[A-Za-z0-9_.-]+\.zip', html)))
    print(f"  Liens ZIP trouvés ({len(zips)}) :", zips[:12])

    # Télécharger la liste Standard XML ou TXT (la plus légère)
    target = None
    for z in zips:
        if "standard_rating_list_xml" in z:
            target = z
            break
    if not target:
        for z in zips:
            if "standard_rating_list" in z:
                target = z
                break
    if not target:
        print("  Aucun ZIP standard trouvé.")
        return
    if target.startswith("/"):
        target = "https://ratings.fide.com" + target

    t0 = time.monotonic()
    st_z, _, b_z = fetch(target, timeout=60)
    dt_dl = time.monotonic() - t0
    print(f"  Téléchargement {target} -> HTTP {st_z}, {len(b_z)/1e6:.2f} Mo en {dt_dl:.2f}s")
    if st_z != 200:
        return

    t1 = time.monotonic()
    with zipfile.ZipFile(io.BytesIO(b_z)) as zf:
        names = zf.namelist()
        print("  Contenu ZIP:", names)
        raw = zf.read(names[0])

    if names[0].endswith(".xml"):
        players = []
        fra_players = []
        total = 0
        active = 0
        for _, elem in ET.iterparse(io.BytesIO(raw), events=("end",)):
            if elem.tag == "player":
                total += 1
                flag = (elem.findtext("flag") or "").strip()
                rtg_s = (elem.findtext("rating") or "").strip()
                if "i" not in flag.lower() and rtg_s.isdigit():
                    active += 1
                    rtg = int(rtg_s)
                    fed = (elem.findtext("country") or "").strip()
                    if rtg >= 2650 or (fed == "FRA" and rtg >= 2500):
                        rec = (
                            rtg,
                            (elem.findtext("name") or "").strip(),
                            fed,
                            (elem.findtext("title") or "").strip(),
                            (elem.findtext("fideid") or "").strip(),
                            (elem.findtext("birthday") or "").strip(),
                        )
                        if rtg >= 2650:
                            players.append(rec)
                        if fed == "FRA":
                            fra_players.append(rec)
                elem.clear()
        dt_parse = time.monotonic() - t1
        players.sort(reverse=True)
        fra_players.sort(reverse=True)
        print(f"  Parsing XML: {total} joueurs ({active} actifs) en {dt_parse:.2f}s")
        print("  Top 5 mondial (Standard actif) :")
        for r in players[:5]:
            print(f"    {r[0]} | {r[3]:3s} {r[1]} ({r[2]}, id={r[4]}, né {r[5]})")
        print("  Top 5 France (Standard actif) :")
        for r in fra_players[:5]:
            print(f"    {r[0]} | {r[3]:3s} {r[1]} (id={r[4]}, né {r[5]})")
    else:
        lines = raw.decode("latin-1", errors="replace").splitlines()
        print(f"  TXT: {len(lines)} lignes, en-tête: {lines[0][:120]}")
        if len(lines) > 1:
            print(f"  Ligne 2: {lines[1][:120]}")


def sonde_stockfish() -> None:
    print("\n" + "=" * 72)
    print("3. STOCKFISH (CI ubuntu-latest)")
    print("=" * 72)
    sf_path = shutil.which("stockfish")
    print(f"  Binaire trouvé : {sf_path}")
    if not sf_path:
        return

    # Évaluer 10 positions d'une partie type (Ruy Lopez Berlin) à profondeur 16
    moves = [
        "e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "g8f6", "e1g1", "f6e4",
        "d2d4", "e4d6", "b5c6", "d7c6", "d4e5", "d6f5", "d1d8", "e8d8",
        "h2h3", "c8d7", "f1d1", "d8c8",
    ]
    proc = subprocess.Popen(
        [sf_path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert proc.stdin and proc.stdout

    def send(cmd: str) -> None:
        proc.stdin.write(cmd + "\n")
        proc.stdin.flush()

    send("uci")
    sf_id = ""
    for line in proc.stdout:
        line = line.strip()
        if line.startswith("id name "):
            sf_id = line
        if line == "uciok":
            break
    print(f"  Version : {sf_id}")
    send("setoption name Threads value 2")
    send("setoption name Hash value 64")
    send("isready")
    for line in proc.stdout:
        if line.strip() == "readyok":
            break

    t0 = time.monotonic()
    evals = []
    for i in range(1, len(moves) + 1):
        prefix = " ".join(moves[:i])
        send(f"position startpos moves {prefix}")
        send("go depth 16")
        last_score = None
        for line in proc.stdout:
            line = line.strip()
            if " score " in line:
                m = re.search(r"score (cp|mate) (-?\d+)", line)
                if m:
                    last_score = (m.group(1), int(m.group(2)))
            if line.startswith("bestmove"):
                break
        evals.append(last_score)
    dt = time.monotonic() - t0
    send("quit")
    proc.wait(timeout=5)
    print(f"  20 demi-coups (10 coups) analysés à depth 16 en {dt:.2f}s ({dt/len(moves)*1000:.0f} ms/demi-coup)")
    print(f"  Extrait évaluations (5 premiers demi-coups) : {evals[:5]}")
    print(f"  Estimation pour une partie de 40 coups (80 demi-coups) : ~{dt * 4:.1f}s")


def sonde_wikidata_wikipedia() -> None:
    print("\n" + "=" * 72)
    print("4. WIKIDATA (P1440) + WIKIPÉDIA FR")
    print("=" * 72)
    fide_ids = ["12573981", "623539", "36085755", "605506", "629065"]
    values = " ".join(f'"{fid}"' for fid in fide_ids)
    query = f"""
    SELECT ?fide ?item ?itemLabel ?dob ?image ?wpfr WHERE {{
      VALUES ?fide {{ {values} }}
      ?item wdt:P1440 ?fide .
      OPTIONAL {{ ?item wdt:P569 ?dob . }}
      OPTIONAL {{ ?item wdt:P18 ?image . }}
      OPTIONAL {{
        ?wpfr schema:about ?item ;
              schema:isPartOf <https://fr.wikipedia.org/> .
      }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "fr,en". }}
    }}
    """
    url = "https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(query)
    st, _, body = fetch(url, accept="application/sparql-results+json")
    print(f"  Wikidata SPARQL -> HTTP {st} ({len(body)} o)")
    if st != 200:
        print("  Erreur:", body[:200])
        return
    rows = json.loads(body.decode("utf-8"))["results"]["bindings"]
    titres_fr = []
    seen = set()
    for r in rows:
        fid = r["fide"]["value"]
        if fid in seen:
            continue
        seen.add(fid)
        wp = r.get("wpfr", {}).get("value", "")
        titre = urllib.parse.unquote(wp.rsplit("/wiki/", 1)[-1]) if "/wiki/" in wp else ""
        if titre:
            titres_fr.append(titre)
        print(
            f"    FIDE {fid:8s} -> {r['item']['value'].rsplit('/', 1)[-1]} | "
            f"{r.get('itemLabel', {}).get('value')} | dob={r.get('dob', {}).get('value', '')[:10]} | "
            f"img={'oui' if 'image' in r else 'non'} | wpfr={titre}"
        )

    if titres_fr:
        time.sleep(1.0)
        wp_url = (
            "https://fr.wikipedia.org/w/api.php?action=query&format=json"
            "&prop=extracts|info&exintro=1&explaintext=1&redirects=1&inprop=url"
            "&titles=" + urllib.parse.quote("|".join(titres_fr))
        )
        st_w, _, b_w = fetch(wp_url)
        print(f"  Wikipédia FR lot ({len(titres_fr)} titres) -> HTTP {st_w} ({len(b_w)} o)")
        if st_w == 200:
            pages = json.loads(b_w.decode("utf-8")).get("query", {}).get("pages", {})
            for p in pages.values():
                ext = (p.get("extract") or "").replace("\n", " ")[:120]
                print(f"    {p.get('title')}: {ext}...")


def main() -> int:
    sonde_lichess()
    sonde_fide()
    sonde_stockfish()
    sonde_wikidata_wikipedia()
    print("\nSonde terminée.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
