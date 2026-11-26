# Étude de faisabilité : Dashboard Échecs (sur le modèle de `coupes-europe`)

Date : 26 novembre 2026.  
Références : `README.md`, `CLAUDE.md`, `Projet_foot.md`, `docs/cahier_des_charges_sources.md` du dépôt `Camelbaggy/coupes-europe`.

> [!NOTE]
> **Méthodologie & transparence** : l'environnement d'exécution de cette session ne permet pas d'appeler directement les API externes depuis le terminal ; les informations ci-dessous s'appuient sur la documentation officielle vérifiée ce jour (Lichess OpenAPI / API Tips / Open Database, page de téléchargement FIDE `ratings.fide.com`, Chess.com PubAPI). Tout ce qui nécessite une mesure directe depuis GitHub Actions (taux de présence des `[%eval]` dans les broadcasts, en-têtes CORS exacts, latence) est explicitement marqué **« à mesurer en étape 0 »**.

---

## 1. Verdict global

**Faisabilité : très élevée** — le domaine des échecs se prête **encore mieux** que le football à l'architecture `coupes-europe` (Python stdlib + GitHub Actions + GitHub Pages statique + ntfy + Gemini contrôlé), pour trois raisons :

1. **Une source principale ouverte, gratuite, officielle et documentée** : l'API **Lichess Broadcasts** couvre la quasi-totalité des grands tournois sur échiquier (OTB) dans le monde, sans clé obligatoire en lecture, sans protection anti-robot, avec des exports de base sous licence **CC0**.
2. **Des données très légères** : une partie complète (coups, pendules, évaluations moteur) pèse ~2 à 4 Ko en JSON compact (~250 Ko pour un super-tournoi entier de 91 parties), bien moins que les 4,7 Mo par match de l'UEFA.
3. **Un écosystème de métriques ouvertes et déterministes** : la courbe d'évaluation Stockfish, les probabilités de gain et la formule de précision (%) de Lichess sont publiques et mathématiques — en parfaite cohérence avec la règle cardinale **« zéro hallucination »**.

**Trois points d'attention spécifiques aux échecs** nécessitent toutefois des choix clairs avant de coder :
- le **statut juridique des listes Elo FIDE** (téléchargement libre mais pied de page restrictif) ;
- la **disponibilité des évaluations moteur `[%eval]`** (présentes sur la plupart des grands broadcasts Lichess, mais pas 100 % — option Stockfish en CI à trancher) ;
- la **prévention des hallucinations tactiques de Gemini** (un LLM invente très vite des coups ou des menaces s'il lit un PGN brut sans dossier factuel pré-calculé).

---

## 2. Transposition fonctionnelle : Football → Échecs

| Concept `coupes-europe` | Équivalent Échecs | Remarques |
|---|---|---|
| Compétitions (LDC, Big 5, CdM…) | **Tournois & circuits** (Championnat du monde, Candidats, Coupe du monde, Grand Swiss, Olympiade, Tata Steel, Norway Chess, Grand Chess Tour, Championnat de France…) | Filtrage par catégorie (Super-tournois / Cycles FIDE / Nationaux / Rapide & Blitz / Féminin). |
| Soirée / Journée (`tour=j5`) | **Ronde** (`ronde=r5`, ou journée pour Rapide/Blitz) | 1 ronde/jour en classique, plusieurs rondes/jour en rapide/blitz. |
| Focus clubs français (`countryCode == "FRA"`) | **Focus joueurs français (`fed == "FRA"`)** | Détection 100 % automatique via la fédération FIDE (Firouzja, MVL, Maurizzi, Bacrot, Moussard, Guichard, Milliet…) — aucune liste manuelle à maintenir. |
| Drapeaux (`flagcdn.com`) | **Drapeaux FIDE** (`flagcdn.com` + table de correspondance FIDE → ISO) | Attention : quelques codes FIDE diffèrent de l'ISO (ex. `GER`→`de`, `SUI`→`ch`, `NED`→`nl`, `ENG`/`SCO`/`WLS`, `FID` drapeau neutre). |
| Fiche de match : **Déroulé** | **Coups & moments clés** (ouverture ECO, bascules d'évaluation, crises de temps, fin de partie) | Navigation coup par coup ou saut direct aux tournants. |
| Fiche de match : **Compos tactiques** | **Échiquier interactif rejouable** (pièces SVG, navigation `◀ ▶`, retournement, pendules) | Remplace le terrain de foot ; lisible sur mobile 390 px. |
| Fiche de match : **Momentum** | **Courbe d'évaluation / Chances de gain** (Stockfish `[%eval]`) | Même graphique SVG bicolore (Blancs / Noirs) sur l'axe des coups ! |
| Fiche de match : **Stats + Notes (`ratings.py`)** | **Précision (%)**, ACPL, imprécisions / erreurs / gaffes, **gestion du temps** (temps par coup depuis `[%clk]`), **performance Elo** | Formules publiques déterministes ; absent si pas d'`[%eval]` ou pas de `[%clk]`. |
| Résumé IA (`agents_flash.py`) | **Résumé de partie (4-5 phrases)** des Français et affiches majeures | Rédacteur Gemini Flash + contrôleur Flash-Lite sur dossier factuel strict, repli déterministe. |
| Classement (Phase de ligue / Groupes / Arbre) | **Classement du tournoi** (Toutes rondes / Grille américaine, Suisse avec départages, **Arbre** pour Coupe du monde / matchs KO, **Score du match** pour CdM) | 3 composants d'affichage selon le format du tournoi. |
| Fiches club & joueur (`clubs.py`, `encyclo_joueurs.py`) | **Fiches joueur** (ID FIDE, titre GM/IM/WGM…, Elo STD/RPD/BLZ, historique des tournois suivis, stats Blancs/Noirs, Wikidata **`P1440`** + Wikipédia FR + photo Commons) | `P1440` (identifiant FIDE sur Wikidata) est l'équivalent exact de `P7361` (ID UEFA). |
| En direct (`index.html` → ESPN) | **En direct (`index.html` → Lichess Broadcasts)** | Polling ou stream NDJSON/PGN directement depuis le navigateur, zéro serveur. |
| Notifications ntfy (`notification_jour.py`, `direct_clubs.py`) | **Bilan quotidien ntfy** (1 push/jour après fin des rondes publiées) + **alertes en direct** (optionnel : fin de partie d'un Français) | Même architecture `battement.yml` + `veille_secours.yml` + vérification de publication avant push. |

---

## 3. Audit des sources de données

Évaluation selon la grille de `docs/cahier_des_charges_sources.md` :

### A. Lichess Broadcasts API (`https://lichess.org/api/broadcast/...`) — **Source principale retenue**

| Critère | Constat |
|---|---|
| **Format** | API JSON + NDJSON + PGN (OpenAPI 3.2 officielle, dépôt `lichess-org/api`). |
| **Clé / Quotas** | **Aucune clé** en lecture publique. Règles officielles (`lichess.org/page/api-tips`) : **1 requête à la fois** (séquentiel), pause de **60 s** en cas de HTTP 429. |
| **Licence / Conditions** | Plateforme associative open-source (AGPL). Exports de la base (`database.lichess.org`, section Broadcasts) sous licence **CC0** (domaine public). Crédit « Lichess » recommandé. |
| **Anti-robot** | **Aucun** (API conçue pour les clients tiers et scripts automatisés). |
| **Endpoints clés** | `GET /api/broadcast/top` (tournois actifs, à venir, passés, avec `tier` d'importance) ; `GET /api/broadcast/{tourId}` (métadonnées + liste des rondes) ; `GET /api/broadcast/{tourId}/players` (joueurs, ID FIDE, fédération, titre, Elo, score, départages) ; `GET /api/broadcast/round/{roundId}.pgn` (parties de la ronde avec `[%clk]` et `[%eval]`) ; `GET /api/stream/broadcast/round/{roundId}.pgn` (direct). |
| **Couverture** | Quasi-exhaustive sur les tournois OTB majeurs (Championnat du monde, Candidats, Coupe du monde, Grand Swiss, Olympiade, Tata Steel, Norway Chess, Grand Chess Tour, Championnat d'Europe, Championnat de France, etc.). |
| **À mesurer (Étape 0)** | Taux réel de présence de `[%eval]`, `[%clk]` et `fideId` selon le `tier` du tournoi ; en-têtes CORS exacts pour le suivi en direct depuis GitHub Pages. |

### B. FIDE — Listes Elo officielles (`https://ratings.fide.com/download_lists.phtml`) — **Complément à valider juridiquement**

| Critère | Constat |
|---|---|
| **Format** | Fichiers ZIP mensuels officiels (TXT et XML) : Standard (~13 Mo), Rapide (~11 Mo), Blitz (~7 Mo), ou combiné (~43-49 Mo). Archives disponibles depuis février 2015. |
| **Contenu** | ID FIDE, nom, fédération (`FRA`), sexe, titre (`g`, `m`, `wg`…), Elo STD/RPD/BLZ, nombre de parties, facteur K, année de naissance, statut d'inactivité. |
| **Point juridique** | Bien que les fichiers soient en téléchargement public sans authentification (utilisés par les logiciels d'arbitrage comme Swiss-Manager), le pied de page du site affiche : *« © 2026 FIDE… No part of this site may be reproduced, stored in a retrieval system or transmitted… without written permission »*. |
| **Recommandation** | **Décision du propriétaire** (même logique que pour l'ÖFBL autrichienne) : soit demander un accord écrit / vérifier le statut auprès de la FIDE, soit s'en passer dans un premier temps en utilisant les Elo fournis par Lichess Broadcasts (qui les récupère pour chaque tournoi) + Wikidata. |

### C. Wikidata & Wikipédia FR — **Source encyclopédique retenue**

| Critère | Constat |
|---|---|
| **Rapprochement** | Propriété Wikidata **`P1440`** (identifiant FIDE officiel) : rapprochement strict, sans ambiguïté (comme `P7361` pour les clubs UEFA). |
| **Contenu** | Date/lieu de naissance, photo Wikimedia Commons (avec auteur/licence), titre, lien Wikipédia FR (extrait CC BY-SA 4.0 via API MediaWiki par lots de 20 titres, cache 30 jours). |
| **Historique** | Pages Wikipédia structurées pour le palmarès historique des Championnats du monde (depuis 1886) et Olympiades, si souhaité en étape 4. |

### D. Lichess Openings (`github.com/lichess-org/chess-openings`) — **Source secondaire retenue**

- Base open-source **CC0** (fichiers TSV légers, quelques centaines de Ko) associant une séquence de coups / EPD au code **ECO** (ex. `C65` — *Ruy Lopez: Berlin Defense*). Utilisable sans aucun appel réseau supplémentaire, traductible en français sur les grandes familles d'ouvertures.

### E. Chess.com Published-Data API (`https://api.chess.com/pub/`) — **Optionnelle (en ligne uniquement)**

- Gratuite, sans clé, `User-Agent` nominatif requis, réutilisation autorisée (mention de non-affiliation + interdiction de reprendre les éléments graphiques/noms de fonctionnalités Chess.com).
- **Limite** : ne couvre que les comptes et tournois **en ligne** (Titled Tuesday, etc.), **pas** les tournois OTB (`chess.com/events` n'est pas dans PubAPI). Utile uniquement si le périmètre inclut les grands tournois en ligne.

### F. Sources écartées (même règle que FotMob / SofaScore)

- **2700chess.com** : pas d'API publique officielle ; le « Live Elo » de référence reposerait sur du scraping HTML. (Alternative 100 % propre : recalculer la variation Elo sur les parties collectées avec la formule FIDE officielle).
- **Chess-Results.com** : pas d'API officielle (service lié au logiciel payant Swiss-Manager), structure HTML variable, tournois fictifs mélangés aux réels.

---

## 4. Valeurs dérivées déterministes (règle « zéro hallucination »)

Toutes les valeurs calculées seront documentées, déterministes et **omises si la donnée source manque** :

1. **Probabilité de gain (courbe de « momentum »)** : calculée uniquement si `[%eval]` est présent dans le PGN, via la formule publique Lichess :
   $$W(cp) = 50 + 50 \cdot \left(\frac{2}{1 + e^{-0{,}00368208 \cdot cp}} - 1\right)$$
   (bornée à 0 % / 100 % sur les mats forcés `#N`).
2. **Précision de jeu (`%`) et erreurs** : calculée coup par coup à partir de la perte de chances de gain ($\Delta W = W_{\text{avant}} - W_{\text{après}}$) selon la formule publique Lichess (`103.1668 * exp(-0.04354 * dW) - 3.1669`), avec comptage déterministe des imprécisions, erreurs et gaffes.
3. **Temps par coup** : dérivé par soustraction des balises `[%clk]` successives (en tenant compte de l'incrément si la cadence est connue ; sinon affichage du temps restant brut, sans inventer de temps consommé).
4. **Performance Elo du tournoi** : formule FIDE standard à partir du score et de la moyenne Elo des adversaires (affichée uniquement si tous les adversaires ont un Elo).
5. **Cas des broadcasts sans `[%eval]`** : deux options à arbitrer :
   - *Option A (stricte, la plus simple)* : pas d'évaluation dans le PGN Lichess → courbe et précision masquées (« Analyse non fournie »).
   - *Option B (enrichissement CI)* : faire tourner **Stockfish** (binaire Linux open-source GPLv3, `sudo apt-get install stockfish` dans GitHub Actions) à profondeur fixe (ex. depth 16-18, ~2-4 s par partie) sur les parties terminées des Français et des super-tournois dépourvus d'`[%eval]`. Totalement déterministe, gratuit et rapide en CI.

---

## 5. Défis techniques spécifiques aux échecs

### 1. Relecture des parties & échiquier interactif sans dépendance externe
- **Côté Python (collecte)** : parser un fichier PGN (en-têtes, coups SAN, commentaires `[%eval]` et `[%clk]`) se fait proprement en Python 3.11 stdlib (~150 lignes) sans dépendance.
- **Côté `index.html` (échiquier mobile)** : pour permettre de rejouer une partie coup par coup sur un échiquier interactif, le navigateur doit convertir les coups SAN (`Nf3`, `O-O`, `exd5`, `e8=Q+`) en positions sur l'échiquier.
  - *Option 1* : écrire un mini-moteur de règles d'échecs léger en JS pur (~300 lignes dans `index.html`, sans dépendance, testé contre une suite de parties réelles incluant roques, prise en passant, promotions, désambiguïsations).
  - *Option 2* : pré-calculer les coups en format source→destination (`e2e4`, `g1f3`, etc.) côté Python lors de la collecte, de sorte qu'`index.html` n'ait besoin que d'un afficheur de ~80 lignes ! *(Très élégant, zéro logique complexe dans le navigateur, mais il reste le cas du direct où le navigateur lit directement le PGN Lichess).*

### 2. Prévention des hallucinations IA (Gemini)
Aux échecs, un LLM qui lit un PGN brut invente fréquemment des coups illégaux ou de fausses menaces tactiques. Pour respecter la règle cardinale de `agents_flash.py` :
- **Dossier factuel strict** donné à Gemini : joueurs (titres, Elo, fédération, rang au tournoi), ouverture (nom ECO), résultat et cause (mat / abandon / nulle / temps si connue), nombre de coups, précision %, liste des **3 à 5 tournants objectifs** (numéro de coup, coup joué, évaluation avant/après), crise de temps éventuelle (temps restant au coup N), impact sur le classement.
- **Consigne rédacteur** : résumer le *scénario* et l'*enjeu comptable* (4-5 phrases), **sans jamais inventer de variante non jouée**.
- **Contrôleur Flash-Lite** : vérifie que chaque nom, score, numéro de coup, ouverture et chiffre d'évaluation figure dans le dossier.

### 3. Hétérogénéité des tournois et rythme de collecte
- Contrairement au football (matchs de 2 h à dates fixes), une partie d'échecs classique dure entre **1 h et 7 h**, avec des tournois sur tous les fuseaux horaires et des formats variés (toutes rondes, suisse, KO à élimination directe, match).
- Le mécanisme `battement.yml` + `veille_secours.yml` existant s'adapte très bien : la veille interroge `/api/broadcast/top`, suit les rondes actives/terminées des tournois retenus, ne collecte une ronde complète que lorsque toutes ses parties sont terminées (ou partielle passé un délai), puis déclenche la notification unique du jour après vérification de publication.

### 4. Architecture des dépôts & volumétrie
- **Estimation de taille** : ~50 à 80 tournois majeurs/an × ~150 à 300 Ko = **~15 Mo/an** (~3 Mo gzip).
- Un **nouveau dépôt dédié** (ex. `Camelbaggy/echecs-dashboard`, indépendant de `coupes-europe`) est clairement recommandé, éventuellement complété plus tard par un dépôt de données séparé si l'historique ou les listes FIDE dépassent quelques dizaines de Mo.

---

## 6. Feuille de route proposée

| Étape | Périmètre | Livrable principal |
|---|---|---|
| **Étape 0** | **Sonde API réelle** (workflow GitHub Actions temporaire) | Mesure réelle sur 10 tournois Lichess Broadcasts (structure JSON/PGN, présence `fideId`/`fed`/`[%eval]`/`[%clk]`, CORS, quotas) + test Wikidata `P1440`. |
| **Étape 1 (MVP)** | **Collecte & Dashboard mobile** | Collecteur Lichess (`lichess.py`, `run_pipeline.py`), `index.html` mobile (accueil, menu latéral par circuit/tournoi, rondes, classements Toutes rondes / Suisse / KO, fiche de partie avec échiquier rejouable + courbe d'eval + pendules), focus `FRA`, fiabilité (`battement.yml`, ntfy quotidien). |
| **Étape 2** | **Résumés IA & Direct** | `agents_flash.py` adapté aux échecs (dossier factuel + contrôleur + repli déterministe) ; suivi en direct dans `index.html` (polling/stream Lichess) ; alertes ntfy en direct sur les parties de Français (optionnel). |
| **Étape 3** | **Fiches joueurs & Classement mondial** | Fiches joueurs (ID FIDE, parcours dans les tournois suivis, stats Blancs/Noirs, Wikidata `P1440` + Wikipédia FR + photo Commons) ; Top mondial & Top France (selon décision FIDE). |
| **Étape 4** | **Historique & Reconstitution** | Reconstitution des tournois récents passés (API/base CC0 Lichess Broadcasts) et palmarès historique des Championnats du monde. |

---

## 7. Décisions à arbitrer avant de démarrer

1. **Périmètre des tournois** :
   - *Option A (recommandée pour démarrer)* : tournois **OTB majeurs** uniquement (Super-tournois, Cycles FIDE classiques/rapide/blitz, Olympiade, Championnat d'Europe et Championnat de France — via Lichess Broadcasts).
   - *Option B* : ajouter les tournois **en ligne** majeurs (Titled Tuesday, Champions Chess Tour — via Chess.com PubAPI / Lichess).
   - *Option C* : inclure aussi les tournois **féminins** majeurs (Championnat du monde féminin, Candidats féminins, Grand Prix FIDE féminin) dès l'étape 1 ?
2. **Focus & notifications** :
   - Focus automatique sur tous les joueurs de fédération **`FRA`** dans les tournois suivis (comme les clubs français) + top mondial (ex. Top 10 / Championnat du monde) ?
   - Alertes ntfy : bilan quotidien unique seulement, ou aussi alertes en direct pour les fins de partie des Français ?
3. **Analyse moteur (`[%eval]`) si absente du broadcast Lichess** :
   - *Option A* : ne rien calculer (courbe et précision masquées si absentes du PGN).
   - *Option B* : exécuter Stockfish en CI GitHub Actions (profondeur fixe) pour garantir la courbe d'évaluation et la précision sur 100 % des parties suivies.
4. **Classement mondial FIDE (Top 100 / Top France)** :
   - Souhaitez-vous inclure un onglet « Classement Elo mondial / France » (nécessitant de statuer sur l'utilisation des fichiers `ratings.fide.com`, voire de demander un accord à la FIDE), ou vous limiter dans un premier temps aux tournois et aux Elo fournis par Lichess ?
5. **Dépôt GitHub** :
   - Créer un nouveau dépôt dédié (ex. `Camelbaggy/echecs`) dès l'étape 0 ?

---

## 8. Décisions validées (26 novembre 2026)

1. **Périmètre** : démarrage par les **tournois OTB majeurs** (Lichess Broadcasts) ; ajout éventuel des principaux tournois en ligne dans un second temps.
2. **Analyse moteur (`[%eval]`)** : **calcul déterministe** via Stockfish en CI GitHub Actions lorsque les évaluations manquent dans le PGN du broadcast.
3. **Classement mondial / France FIDE** : **onglet mensuel inclus** (sources `ratings.fide.com` validées par le propriétaire).
4. **Notifications** : **aucune notification ntfy** pour ce dashboard.
5. **Dépôt GitHub** : **`Camelbaggy/Echecs`** (`https://github.com/Camelbaggy/Echecs`).

