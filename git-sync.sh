#!/bin/bash
# Send the code of this folder to GitHub (ANDRIYXAA/StudyBooks, branch main).
# Usage:  ./git-sync.sh "what changed"      (no message: "update")
# The first run connects this folder to the repository. It needs no other setup: git uses the stored GitHub login.
# GIT_COAUTHOR_TRAILER="Co-Authored-By: Name <mail>" adds that line to the commit message.
set -euo pipefail
cd "$(dirname "$0")"
REPO="${SB_GITHUB_REPO:-https://github.com/ANDRIYXAA/StudyBooks.git}"
BRANCH=main
MSG="${1:-update}"

if [ ! -d .git ]; then
  git init -q -b "$BRANCH"
  git remote add origin "$REPO"
  git fetch -q origin "$BRANCH"
  git reset -q "origin/$BRANCH"              # take the history of the repository, keep the files of this folder
fi
git remote get-url origin >/dev/null 2>&1 || git remote add origin "$REPO"

git add -A
if git diff --cached --quiet; then
  echo "nothing to commit"
else
  [ -n "${GIT_COAUTHOR_TRAILER:-}" ] && MSG="$MSG"$'\n\n'"$GIT_COAUTHOR_TRAILER"
  git commit -q -m "$MSG"
  git log --oneline -1
fi
git fetch -q origin "$BRANCH"
git rebase -q "origin/$BRANCH" || { echo "rebase stopped: fix the conflict, then run git rebase --continue and this script again"; exit 1; }
git push -q origin "$BRANCH"
echo "pushed: $(git rev-parse --short HEAD) -> $REPO"
