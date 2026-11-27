# CLAUDE.md

Instructions de travail pour le dépôt `Camelbaggy/Echecs`.

## Projet

Dashboard mobile des grands tournois d'échecs OTB (Lichess Broadcasts + historique multi-années du circuit + Stockfish + FIDE mensuel + Wikidata/Wikipédia FR + résumés déterministes), hébergé en statique sur GitHub Pages (`index.html`).
Étude et décisions validées du 26/11/2026 : `docs/etude_faisabilite.md`.
Tout est en français : interface, logs, messages de commit, commentaires.

**Règle cardinale : zéro hallucination sur les chiffres.** Scores, coups, Elo, classements et pendules viennent uniquement des sources officielles. Les valeurs dérivées (probabilité de gain, précision %, ACPL, tournants, évaluations Stockfish, palmarès de carrière) sont 100 % déterministes et documentées. Un champ absent reste vide.

**Pas de notification ntfy** et **pas de résumé par IA** pour ce dashboard.

## Commandes

Python 3.11 bibliothèque standard uniquement (aucune dépendance pip).

```bash
python3 -m py_compile *.py tools/*.py
python3 pgn.py --test && python3 analyse.py --test && python3 lichess.py --test && python3 historique.py --test && python3 fide.py --test && python3 joueurs.py --test && python3 agents_flash.py --test
bash -n .github/scripts/publier.sh
python3 run_pipeline.py --demo /tmp/demo_echecs
python3 run_pipeline.py --tournois --max 6
python3 run_pipeline.py --historique --saison 2026
python3 run_pipeline.py --fide
python3 run_pipeline.py --joueurs
```

## Architecture

- `netutil.py` : requêtes HTTP respectueuses (1 requête à la fois sur Lichess, pause 60 s sur 429), drapeaux FIDE → ISO (`FIDE_TO_ISO`), écriture JSON sans bruit (`save_json_if_changed`).
- `pgn.py` : parseur PGN + moteur de coups légaux SAN → UCI (`Board.push_san`), extraction `[%eval]` / `[%clk]`.
- `analyse.py` : formules publiques Lichess (`win_percent`, `move_accuracy`), détection des tournants objectifs, gestion du temps, et `StockfishRunner` (UCI, profondeur 12) si `[%eval]` manque sur une partie terminée.
- `lichess.py` : sélection des tournois OTB majeurs (4 catégories strictes `fide`, `super`, `europe`, `france`, hors ligues étrangères/jeunes/en ligne), détection automatique des Français (`fed == "FRA"`), écriture `tournois/<id>.json`.
- `historique.py` : collecte multi-années (2021–2026+) de tous les tournois majeurs du circuit via `/api/broadcast/search` + `/broadcast/{id}/players` → `historique/t/<id>.json` + `historique/index.json`.
- `fide.py` : classement mensuel FIDE (`ratings.fide.com` XML avec repli Wikidata `P1440`+`P21` → Lichess `/api/fide/player/{id}`) → `fide/classement.json`.
- `joueurs.py` : agrégation tournois + historique complet + FIDE → `joueurs/<fide_id>.json` + `joueurs/index.json` (parcours complet en tournois majeurs + stats de carrière + Wikidata `P1440`/photos HTTPS/`P1346` + Wikipédia FR, cache 30 jours, jamais bloquant).
- `agents_flash.py` : résumés factuels 100 % déterministes (`build_deterministic_summary`) sans appel IA.
- `index.html` : page autonome mobile-first, 3 vues (Accueil avec tout le circuit + archives filtrables par catégorie et année, Tournoi, Classement FIDE mensuel), 2 feuilles empilables fermables par glissement vers le bas (`#game-sheet`, `#player-sheet`), suivi en direct Lichess côté navigateur, info-bulle unique `infoBtn`.

## Workflows GitHub Actions

1. `collecte_tournois.yml` (toutes les 2 h + manuel) : tournois OTB majeurs en cours/récents + Stockfish.
2. `historique_tournois.yml` (lundi + manuel) : historique multi-années du circuit (`historique/`) + parties complètes de la saison en cours + palmarès carrière des joueurs.
3. `classement_fide.yml` (1er et 2 du mois + manuel) : classement FIDE mensuel.
4. `fiches_joueurs.yml` (lundi + manuel) : enrichissement Wikidata (photos HTTPS, palmarès pré-2021) & Wikipédia FR.
