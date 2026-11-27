#!/usr/bin/env bash
# Commit et push robuste sur main (avec rebase -X theirs si un autre workflow a poussé entre-temps).
set -euo pipefail

MSG="${1:-chore: mise à jour automatique}"
shift || true
PATHS=("$@")
if [ "${#PATHS[@]}" -eq 0 ]; then
  PATHS=(tournois fide joueurs)
fi

git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"

for p in "${PATHS[@]}"; do
  [ -e "$p" ] && git add "$p"
done

if git diff --cached --quiet; then
  echo "[PUBLIER] Aucun changement à committer."
  exit 0
fi

git commit -m "$MSG"

for essai in 1 2 3; do
  if git push origin HEAD:main; then
    echo "[PUBLIER] Push réussi (essai $essai)."
    exit 0
  fi
  echo "[PUBLIER] Conflit push (essai $essai), rebase sur origin/main…"
  git fetch origin main
  git rebase -X theirs origin/main
  if [ -d tournois ]; then
    python3 -c "import run_pipeline; run_pipeline.reindexer_tournois()" || true
    git add tournois/index.json || true
    git diff --cached --quiet || git commit --amend --no-edit
  fi
done

echo "[PUBLIER] Échec après 3 essais."
exit 1
