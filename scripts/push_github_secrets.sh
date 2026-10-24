#!/usr/bin/env bash
# .env ফাইলের মানগুলো GitHub রিপোতে Secrets/Variables হিসেবে পুশ করে।
#
#   1) cp .env.example .env   → মান বসান
#   2) gh auth login
#   3) ./scripts/push_github_secrets.sh
#
# gh CLI ইনস্টল না থাকলে: https://cli.github.com
set -euo pipefail

ENV_FILE="${1:-.env}"
SECRET_KEYS=(TG_API_ID TG_API_HASH TG_STRING_SESSION BOT_TOKEN AI_API_KEY)
VAR_KEYS=(
  TARGET_CHANNEL SOURCE_CHANNELS OWNER_USER_IDS AI_PROVIDER AI_MODEL
  POST_LANGUAGE CHANNEL_TITLE CHANNEL_PERSONA SIGNATURE STYLE_RULES
  HASHTAG_COUNT MAX_POST_CHARS MAX_POSTS_PER_RUN DAILY_POST_LIMIT
  MIN_SOURCE_AGE_MINUTES MAX_SOURCE_AGE_HOURS BLOCKED_KEYWORDS
  MEDIA_ENABLED TIMEZONE
)

if [[ ! -f "$ENV_FILE" ]]; then
  echo "❌ $ENV_FILE পাওয়া যায়নি – আগে 'cp .env.example .env' চালান" >&2
  exit 1
fi

command -v gh >/dev/null || { echo "❌ gh CLI ইনস্টল নেই" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "❌ আগে 'gh auth login' করুন" >&2; exit 1; }

repo="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
echo "→ রিপো: $repo"
echo "⚠️  মানগুলো GitHub-এ যাবে। .env ফাইলটা কখনো কমিট করবেন না (.gitignore-এ আছে)।"
echo

get_value() {
  # শেষ যেটা মিলেছে সেটাই নেয় (STYLE_RULES একাধিকবার থাকতে পারে → সব জোড়া লাগে)
  local key="$1"
  awk -F= -v k="$key" '
    $0 ~ "^[[:space:]]*"k"=" {
      val = substr($0, index($0, "=") + 1)
      sub(/^[[:space:]]+/, "", val); sub(/[[:space:]]+$/, "", val)
      if (val != "") { out = (out == "" ? val : out "," val) }
    }
    END { if (out != "") print out }
  ' "$ENV_FILE"
}

for key in "${SECRET_KEYS[@]}"; do
  value="$(get_value "$key")"
  if [[ -z "$value" ]]; then
    echo "⏭  secret $key খালি – বাদ"
    continue
  fi
  gh secret set "$key" --repo "$repo" --body "$value"
  echo "✅ secret $key"
done

for key in "${VAR_KEYS[@]}"; do
  value="$(get_value "$key")"
  if [[ -z "$value" ]]; then
    continue
  fi
  gh variable set "$key" --repo "$repo" --body "$value"
  echo "✅ variable $key"
done

echo
echo "👉 এখন: Actions → Autopost → Run workflow (dry_run=true দিয়ে শুরু করুন)"
