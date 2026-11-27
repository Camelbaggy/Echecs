# Echecs

Tableau de bord mobile des grands tournois d'échecs sur échiquier (OTB), centré sur les joueurs français (`FRA`) et accompagné du classement mensuel officiel FIDE.  
Conçu sur le modèle de [Camelbaggy/coupes-europe](https://github.com/Camelbaggy/coupes-europe) : Python 3.11 bibliothèque standard uniquement, hébergement statique GitHub Pages (`index.html`), zéro hallucination sur les chiffres, aucune notification push.

## Fonctionnement

| Module | Rôle |
|---|---|
| `lichess.py` | Collecte des tournois OTB majeurs (Lichess Broadcasts API : métadonnées, rondes, classements, PGN) |
| `pgn.py` | Parseur PGN + moteur de coups légaux SAN → UCI (roques, prise en passant, promotions, clouages) |
| `analyse.py` | Courbe de chances de gain & précision (%) selon les formules publiques Lichess, pendules, et repli déterministe **Stockfish** (UCI) si `[%eval]` manque |
| `fide.py` | Collecte mensuelle des classements officiels FIDE (Classique, Rapide, Blitz : Monde, France, Femmes, Juniors) |
| `joueurs.py` | Fiches joueurs (`joueurs/<fide_id>.json`) agrégées + Wikidata (`P1440`) et Wikipédia FR (lots MediaWiki, cache 30 j) |
| `agents_flash.py` | Résumés factuels (3–4 phrases) des parties de Français et affiches majeures : Gemini Flash rédacteur + Flash-Lite contrôleur, repli déterministe |
| `run_pipeline.py` | Orchestrateur (`--tournois`, `--fide`, `--joueurs`, `--demo DIR`) |
| `index.html` | Dashboard mobile autonome (échiquier interactif, courbe d'évaluation SVG, classement FIDE mensuel, fiches joueurs, suivi en direct Lichess) |

## Sources de données

- **Lichess Broadcasts API** (principale, publique, sans clé en lecture, 1 requête à la fois) : tournois OTB majeurs, rondes, joueurs (`fideId`, fédération `FRA`, Elo, score), PGN avec `[%eval]` et `[%clk]`.
- **FIDE (`ratings.fide.com`)** : listes XML mensuelles officielles (Classique, Rapide, Blitz).
- **Stockfish (UCI)** : installé en CI GitHub Actions pour calculer les évaluations de manière déterministe lorsque `[%eval]` est absent du broadcast.
- **Wikidata (`P1440`) & Wikipédia FR (CC BY-SA 4.0)** : fiches encyclopédiques et photos Commons.
- **Drapeaux** : `flagcdn.com` via table FIDE → ISO (`netutil.py`).

## Workflows GitHub Actions

- `collecte_tournois.yml` : toutes les 2 heures + manuel (`tournois/`, `joueurs/`).
- `classement_fide.yml` : les 1er et 2 de chaque mois + manuel (`fide/classement.json`).
- `fiches_joueurs.yml` : chaque lundi + manuel (`joueurs/` avec Wikidata/Wikipédia FR).
- `sonde_api.yml` : sonde diagnostique des API (manuel).

Secret optionnel : `GEMINI_API_KEY` (sans clé, les résumés sont générés de façon déterministe).

## Lancer en local

```bash
python3 pgn.py --test && python3 analyse.py --test && python3 lichess.py --test && python3 fide.py --test && python3 joueurs.py --test && python3 agents_flash.py --test
python3 run_pipeline.py --demo /tmp/demo_echecs
python3 -m http.server 8000   # puis ouvrir http://localhost:8000/?donnees=/tmp/demo_echecs (ou copier dans un dossier servi)
```