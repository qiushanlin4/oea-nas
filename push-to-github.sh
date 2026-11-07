#!/bin/bash
# push-to-github.sh — one-shot publish of the oea-nas-docs repository.
#
#   GITHUB_TOKEN=ghp_xxxx ./push-to-github.sh [repo-name]
#
# What it does:
#   1. resolves the authenticated username from the token
#   2. creates a PUBLIC repo (if it does not exist yet)
#   3. pushes branch `main` with the token embedded in the URL
#   4. ALWAYS scrubs the token from .git/config afterwards (trap)
#   5. verifies the result through the GitHub API
#
# The token is never echoed, never written to a file, and never committed.

set -u

REPO="${1:-oea-nas}"
REPO="${REPO// /-}"
TOKEN="${GITHUB_TOKEN:-}"

if [ -z "$TOKEN" ]; then
    echo "ERROR: set GITHUB_TOKEN first" >&2
    exit 1
fi

API="https://api.github.com"
AUTH="Authorization: Bearer $TOKEN"
ACCEPT="Accept: application/vnd.github+json"

# --- resolve user ------------------------------------------------------------
USER_JSON=$(curl -sS -m 30 -H "$AUTH" -H "$ACCEPT" "$API/user")
USER_NAME=$(printf '%s' "$USER_JSON" | sed -n 's/.*"login"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)

if [ -z "$USER_NAME" ]; then
    echo "ERROR: token rejected or /user did not return a login." >&2
    printf '%s' "$USER_JSON" | head -c 400 >&2; echo >&2
    exit 1
fi
echo "authenticated as: $USER_NAME"

# token scopes (informational only)
SCOPES=$(curl -sS -m 20 -I -H "$AUTH" "$API/rate_limit" 2>/dev/null | grep -i '^x-oauth-scopes:' | tr -d '\r' | cut -d: -f2-)
echo "token scopes:${SCOPES:- (none reported)}"

# --- create the public repo (422 = already exists, which is fine) -------------
DESC="网心云 OEA (RK3318 / 1GB / 128GB eMMC) 刷成 Armbian + Samba 本地 NAS：FIQ debugger → fastboot → rootfs 替换 → 111GB mergerfs 池。完整逆向记录、脚本与踩坑清单。"
CREATE_PAYLOAD=$(printf '{"name":"%s","description":"%s","private":false,"has_issues":true,"has_wiki":false,"has_projects":false}' \
    "$REPO" "$DESC")

HTTP=$(curl -sS -m 30 -o /tmp/gh_create.json -w '%{http_code}' \
    -X POST -H "$AUTH" -H "$ACCEPT" -H 'Content-Type: application/json' \
    -d "$CREATE_PAYLOAD" "$API/user/repos")

case "$HTTP" in
    201) echo "repo created: $USER_NAME/$REPO" ;;
    422) echo "repo already exists: $USER_NAME/$REPO (will push into it)" ;;
    *)  echo "ERROR: create repo returned HTTP $HTTP" >&2
        head -c 500 /tmp/gh_create.json >&2; echo >&2
        exit 1 ;;
esac

# --- push ---------------------------------------------------------------------
REMOTE_URL="https://github.com/$USER_NAME/$REPO.git"
SCRUB() {
    # never leave the token in .git/config, on success or failure
    git remote set-url origin "$REMOTE_URL" 2>/dev/null || true
    echo "(token scrubbed from git config)"
}
trap SCRUB EXIT

git remote remove origin 2>/dev/null || true
git remote add origin "$REMOTE_URL"

echo "pushing main ..."
# x-access-token form is what GitHub Actions uses; works for classic and
# fine-grained PATs alike. http.extraHeader keeps the token out of the URL
# (and therefore out of .git/config entirely).
B64=$(printf 'x-access-token:%s' "$TOKEN" | base64 -w0 2>/dev/null || printf 'x-access-token:%s' "$TOKEN" | base64 | tr -d '\n')
git -c "http.https://github.com/.extraheader=AUTHORIZATION: basic $B64" \
    push -u origin main 2>&1 | sed -E "s/$TOKEN/<TOKEN>/g"

PUSH_RC=${PIPESTATUS[0]}
if [ "$PUSH_RC" -ne 0 ]; then
    echo "ERROR: git push failed with rc=$PUSH_RC" >&2
    exit "$PUSH_RC"
fi

# --- verify -------------------------------------------------------------------
echo
echo "verifying via API ..."
curl -sS -m 30 -H "$AUTH" -H "$ACCEPT" "$API/repos/$USER_NAME/$REPO" \
    | sed -n 's/.*"full_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/repo:     \1/p;
             s/.*"default_branch"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/branch:   \1/p;
             s/.*"html_url"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/url:      \1/p;
             s/.*"size"[[:space:]]*:[[:space:]]*\([0-9]*\).*/size_kb:  \1/p' | head -6

echo
echo "DONE -> https://github.com/$USER_NAME/$REPO"
