#!/usr/bin/env bash
# Ship to production BY HAND -- the escape hatch, not the normal route.
#
# The normal route is: merge to main. That runs the tests, builds, deploys, and
# then polls /api/health until production reports the exact commit it shipped,
# failing loudly if it does not. Use it. This script exists for the times you
# cannot (GitHub Actions is down, or you need a release right now), and its job
# is to make a hand deploy behave like that one instead of like `fly deploy`.
#
# WHY THIS EXISTS. `fly deploy` uploads THE FILES ON THIS MACHINE. It does not
# read GitHub. Run it from a checkout that is behind main and it quietly
# replaces the released code with whatever you happen to have -- no tests, no
# commit stamp, no verification, and nothing anywhere says so. On 2026-08-27
# that put a pre-Aug-24 build back into production minutes before a seller hit
# publish, and the eBay error it caused looked identical to the bug that had
# just been fixed.
#
#   ./deploy.sh          check everything, then deploy
#   ./deploy.sh --check  check only, change nothing
set -euo pipefail
cd "$(dirname "$0")"

APP="${FLY_APP:-listing-lfwjrg}"
SITE="${SITE:-https://listing-lfwjrg.fly.dev}"
BRANCH="main"
check_only=false
[ "${1:-}" = "--check" ] && check_only=true

die() { echo "REFUSING: $*" >&2; exit 1; }

# The git checks come first, and flyctl is only required to actually deploy, so
# `--check` works anywhere -- including in CI and on a machine without flyctl.
# It also puts the useful message first: "your branch is behind" is what you
# need to hear, not "install a tool" when the tree was wrong anyway.

# 1. On main. A deploy from a feature branch ships unreviewed work to sellers.
current=$(git rev-parse --abbrev-ref HEAD)
[ "$current" = "$BRANCH" ] \
  || die "you are on '$current', not $BRANCH. git checkout $BRANCH"

# 2. Nothing uncommitted. Whatever is in the working tree is what gets shipped,
#    so an experiment you forgot about goes live with it.
git diff --quiet && git diff --cached --quiet \
  || die "you have uncommitted changes. Commit or stash them first
         (they WOULD be deployed -- fly ships your files, not your commits)."

# 3. Up to date with the remote. This is the one that actually bit us: being
#    behind main is invisible locally and silently reverts everyone else's work.
echo "Fetching $BRANCH..."
git fetch --quiet origin "$BRANCH"
behind=$(git rev-list --count HEAD..origin/$BRANCH)
ahead=$(git rev-list --count origin/$BRANCH..HEAD)
[ "$behind" = "0" ] || die "your $BRANCH is $behind commit(s) BEHIND origin/$BRANCH.
         Deploying now would roll production back. Run: git pull origin $BRANCH"
[ "$ahead" = "0" ] || die "your $BRANCH is $ahead commit(s) AHEAD of origin/$BRANCH.
         Push and let CI deploy it, so what ships is what was reviewed."

sha=$(git rev-parse HEAD)
echo "OK: $BRANCH is clean and matches origin at ${sha:0:8}."
$check_only && { echo "--check: stopping before deploy."; exit 0; }

command -v fly >/dev/null 2>&1 || command -v flyctl >/dev/null 2>&1 \
  || die "flyctl is not installed."
FLY=$(command -v fly 2>/dev/null || command -v flyctl)

# 4. GIT_SHA is not optional. Without it BUILD_SHA is empty, /api/health reports
#    no commit, and both the deploy gate and the health watch's drift check have
#    nothing to compare -- which is how a hand deploy became invisible.
echo "Deploying $sha to $APP..."
"$FLY" deploy --remote-only -a "$APP" --build-arg "GIT_SHA=$sha"

# 5. Same verification the CI deploy does. "fly deploy said OK" is not evidence
#    that production is serving it.
echo "Verifying production is running $sha..."
for i in $(seq 1 20); do
  got=$(curl -sS -m 15 "$SITE/api/health" \
    | python3 -c 'import json,sys;print((json.load(sys.stdin).get("build") or "").strip())' \
    2>/dev/null || echo '')
  echo "  attempt $i: running=${got:-<none>}"
  if [ "$got" = "$sha" ]; then
    echo "OK: production is running $sha"
    exit 0
  fi
  sleep 15
done
echo "ERROR: deploy reported success but production is NOT running $sha." >&2
echo "The image was built and pushed; the machine is serving something else." >&2
exit 1
