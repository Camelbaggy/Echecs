# CLAUDE.md

Instructions de travail pour le dépôt `Camelbaggy/Echecs`.

## Projet

Dashboard mobile des grands tournois d'échecs OTB (Lichess Broadcasts + Stockfish + FIDE mensuel + Wikidata/Wikipédia FR + résumés Gemini contrôlés), hébergé en statique sur GitHub Pages (`index.html`).
Étude et décisions validées du 26/11/2026 : `docs/etude_faisabilite.md`.
Tout est en français : interface, logs, messages de commit, commentaires.

**Règle cardinale : zéro hallucination sur les chiffres.** Scores, coups, Elo, classements et pendules viennent uniquement des sources officielles. Les valeurs dérivées (probabilité de gain, précision %, ACPL, tournants, évaluations Stockfish) sont 100 % déterministes et documentées. Un champ absent reste vide.

**Pas de notification ntfy** pour ce dashboard (décision du 26/11/2026).

## Commandes

Python 3.11 bibliothèque standard uniquement (aucune dépendance pip).

```bash
python3 -m py_compile *.py tools/*.py
python3 pgn.py --test && python3 analyse.py --test && python3 lichess.py --test && python3 fide.py --test && python3 joueurs.py --test && python3 agents_flash.py --test
bash -n .github/scripts/publier.sh
python3 run_pipeline.py --demo /tmp/demo_echecs
python3 run_pipeline.py --tournois --sans-ia --max 6
python3 run_pipeline.py --fide
python3 run_pipeline.py --joueurs
```

## Architecture

- `netutil.py` : requêtes HTTP respectueuses (1 requête à la fois sur Lichess, pause 60 s sur 429), drapeaux FIDE → ISO (`FIDE_TO_ISO`), écriture JSON sans bruit (`save_json_if_changed`).
- `pgn.py` : parseur PGN + moteur de coups légaux SAN → UCI (`Board.push_san`), extraction `[%eval]` / `[%clk]`.
- `analyse.py` : formules publiques Lichess (`win_percent`, `move_accuracy`), détection des tournants objectifs, gestion du temps, et `StockfishRunner` (UCI, profondeur 14) si `[%eval]` manque sur une partie terminée.
- `lichess.py` : sélection des tournois OTB majeurs (`est_tournoi_majeur_otb`, `tier >= 4` ou circuit majeur, hors en ligne), détection automatique des Français (`fed == "FRA"`), écriture `tournois/<id>.json`.
- `fide.py` : découverte dynamique des URLs XML sur `ratings.fide.com/download_lists.phtml`, parsing streaming `ET.iterparse` → `fide/classement.json`.
- `joueurs.py` : agrégation tournois + FIDE → `joueurs/<fide_id>.json` + `joueurs/index.json` ; enrichissement Wikidata (`P1440`) et Wikipédia FR par lots de 20 (cache 30 jours, jamais bloquant).
- `agents_flash.py` : dossier factuel strict (`build_game_dossier`) → Gemini Flash rédacteur + Flash-Lite contrôleur (3 essais), repli déterministe si clé absente / quota / rejets.
- `index.html` : page autonome mobile-first, 3 vues (Accueil, Tournoi, Classement FIDE mensuel), 2 feuilles empilables (`#game-sheet` avec échiquier interactif + courbe SVG + feuille de coups, `#player-sheet`), suivi en direct Lichess côté navigateur, info-bulle unique `infoBtn`.

## Premier lancement sur GitHub Actions après push

1. Activer GitHub Pages sur la branche `main` (racine `/`).
2. Lancer `classement_fide.yml` (`gh workflow run classement_fide.yml --ref main`).
3. Lancer `collecte_tournois.yml` (`gh workflow run collecte_tournois.yml --ref main`).
4. Lancer `fiches_joueurs.yml` (`gh workflow run fiches_joueurs.yml --ref main`).
5. Optionnel : ajouter le secret `GEMINI_API_KEY` pour activer les résumés Gemini (sans clé, les résumés déterministes fonctionnent déjà).
