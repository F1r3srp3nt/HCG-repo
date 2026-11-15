#!/usr/bin/env bash
# Read-only transport diagnostics for the HCG Repo domain. Prints a Markdown report on stdout and
# exits non-zero when the APT source is not served correctly over valid HTTPS.
#
#   tools/transport_check.sh [host] [repo-path]
#
# It asks the domain's authoritative nameservers and several public resolvers, then tests every
# address they return, so a difference between resolvers (e.g. a CDN proxy that only some clients
# see yet) shows up instead of being hidden by one cached answer. Certificate verification is never
# turned off for the verdict; an unverified fetch is used only to show what an edge returns.
set -uo pipefail
HOST="${1:-repo.hakmicapitalgroup.com}"
REPO_PATH="${2:-/repo/}"
ZONE="$(echo "$HOST" | awk -F. '{print $(NF-1)"."$NF}')"
fail=0

echo "# Transport check for \`$HOST\` ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
echo
echo "## DNS"
echo '```'
echo "Nameservers for $ZONE: $(dig +short NS "$ZONE" | sort | tr '\n' ' ')"
ips=""
for ns in $(dig +short NS "$ZONE" | sort); do
  ans=$(dig +norecurse +noall +answer @"$ns" "$HOST" A "$HOST" AAAA 2>/dev/null | awk '{print $4, $5}' | tr '\n' ' ')
  echo "authoritative @$ns: ${ans:-<no answer>}"
done
for r in 1.1.1.1 8.8.8.8 9.9.9.9 208.67.222.222; do
  ans=$(dig +noall +answer @"$r" "$HOST" A 2>/dev/null | awk '{print $4, $5, "ttl=" $2}' | tr '\n' ' ')
  echo "resolver $r: ${ans:-<no answer>}"
  ips="$ips $(dig +short @"$r" "$HOST" A 2>/dev/null | grep -E '^[0-9.]+$')"
done
echo '```'
ips=$(echo $ips | tr ' ' '\n' | sort -u | tr '\n' ' ')
echo
echo "## Per address"
echo
echo "| Address | HTTP /repo/Release | Server header | cf-ray | HTTPS (verified) |"
echo "|---|---|---|---|---|"
for ip in $ips; do
  h=$(curl -sS -o /dev/null -m 15 --resolve "$HOST:80:$ip" -D - "http://$HOST${REPO_PATH}Release" 2>&1)
  code=$(echo "$h" | awk 'toupper($1) ~ /^HTTP/ {c=$2} END {print c}')
  server=$(echo "$h" | awk -F': ' 'tolower($1)=="server" {print $2}' | tr -d '\r')
  ray=$(echo "$h" | awk -F': ' 'tolower($1)=="cf-ray" {print $2}' | tr -d '\r')
  t=$(curl -sS -o /dev/null -m 15 --resolve "$HOST:443:$ip" -w '%{http_code}' "https://$HOST${REPO_PATH}Release" 2>&1 | tail -1)
  echo "| $ip | ${code:-none} | ${server:--} | ${ray:--} | $t |"
done
echo
echo "## Certificate presented (SNI $HOST)"
echo '```'
echo | openssl s_client -connect "$HOST:443" -servername "$HOST" -showcerts 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates -ext subjectAltName 2>&1 || echo "no certificate"
echo '```'
echo
echo "## APT endpoints over verified HTTPS"
echo
echo "| URL | Status | Content-Type | First line | Verdict |"
echo "|---|---|---|---|---|"
check_body() {  # url expect-regex label
  local url="$1" expect="$2" tmp code ctype first verdict
  tmp=$(mktemp)
  read -r code ctype < <(curl -sS -m 20 -o "$tmp" -w '%{http_code} %{content_type}\n' "$url" 2>/dev/null || echo "000 -")
  first=$(head -c 200 "$tmp" | head -1 | tr -d '\r|' | cut -c1-60)
  if [ "$code" != 200 ]; then verdict="FAIL (status)"; fail=1
  elif head -c 512 "$tmp" | grep -qiE '<!doctype|<html|<head|<body'; then verdict="FAIL (HTML, not APT data)"; fail=1
  elif [ -n "$expect" ] && ! head -1 "$tmp" | grep -qE "$expect" && [ -s "$tmp" ]; then verdict="FAIL (unexpected content)"; fail=1
  else verdict="OK"; fi
  echo "| \`$url\` | $code | ${ctype:--} | \`${first:-<empty>}\` | $verdict |"
  rm -f "$tmp"
}
base="https://$HOST$REPO_PATH"
check_body "${base}Release" '^(Origin|Label|Suite|Date|Architectures|Components|Description|Codename|Version):'
check_body "${base}Packages" '^Package: '
for f in Packages.xz Packages.gz Packages.bz2 CydiaIcon.png CydiaIcon@2x.png CydiaIcon@3x.png sileo-featured.json; do
  out=$(curl -sS -m 20 -o /dev/null -w '%{http_code} %{content_type}' "${base}$f" 2>/dev/null || echo "000 -")
  code=${out%% *}; ctype=${out#* }
  if [ "$code" = 200 ]; then verdict=OK
  elif [ "$f" = sileo-featured.json ]; then verdict="absent (optional)"
  else verdict=FAIL; fail=1; fi
  echo "| \`${base}$f\` | $code | ${ctype:--} | | $verdict |"
done
echo
echo "## Redirects"
echo '```'
for u in "http://$HOST$REPO_PATH" "https://f1r3srp3nt.github.io/HCG-repo${REPO_PATH}Release"; do
  printf '%-60s %s\n' "$u" "$(curl -sS -o /dev/null -m 15 -w '%{http_code} -> %{redirect_url}' "$u" 2>&1 | tail -1)"
done
echo '```'
echo
[ $fail -eq 0 ] && echo "**Verdict: the APT source is served correctly over valid HTTPS.**" \
                || echo "**Verdict: NOT served correctly over valid HTTPS (see FAIL rows).**"
exit $fail
