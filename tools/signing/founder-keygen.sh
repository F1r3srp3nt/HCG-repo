#!/usr/bin/env bash
# HCG Repo archive key: founder-controlled, offline key generation (docs/SIGNING.md, "Founder procedure").
#
# Run on a trusted, offline machine. It creates:
#   <out>/public/hcg-repo-archive-key.asc        public key            -> commit to HCG-repo keys/
#   <out>/public/FINGERPRINT                     primary fingerprint   -> config/repo.toml [signing] fingerprint
#   <out>/ci/hcg-repo-signing-subkey.asc         signing SUBKEY only   -> GitHub secret HCG_REPO_GPG_PRIVATE_KEY
#   <out>/offline/primary-secret-backup.asc      full secret key (passphrase-protected) -> offline backups only
#   <out>/offline/revocation-certificate.rev     revocation certificate -> offline, separate from the backup
#   <out>/offline/keyring/                       the offline keyring (primary key)
# It never prints secret material, refuses to write inside a Git working tree, and ends with a self-test: the CI
# subkey signs a file and gpgv verifies it against the public key alone.
#
# Usage: founder-keygen.sh <out-dir> [--uid "HCG Repo Archive Key <repo@hakmicapitalgroup.com>"]
#                          [--primary-expire 5y] [--subkey-expire 2y] [--test]
# --test: throw-away keys for development (no passphrase, RSA 2048). Never use --test keys in production.
set -euo pipefail
umask 077

OUT="${1:?usage: founder-keygen.sh <out-dir> [options]}"; shift
UIDSTR="HCG Repo Archive Key <repo@hakmicapitalgroup.com>"
PEXP="5y"; SEXP="2y"; TEST=0; BITS=4096
while [ $# -gt 0 ]; do
  case "$1" in
    --uid) UIDSTR="$2"; shift 2 ;;
    --primary-expire) PEXP="$2"; shift 2 ;;
    --subkey-expire) SEXP="$2"; shift 2 ;;
    --test) TEST=1; BITS=2048; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done

[ -e "$OUT" ] && [ -n "$(ls -A "$OUT" 2>/dev/null)" ] && { echo "refusing: $OUT is not empty" >&2; exit 1; }
PARENT="$(dirname "$OUT")"
[ -d "$PARENT" ] || { echo "refusing: $PARENT does not exist" >&2; exit 1; }
if git -C "$PARENT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "refusing: $OUT is inside a Git working tree; secret keys must never be near a repository" >&2; exit 1
fi
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
mkdir -p "$OUT/public" "$OUT/ci" "$OUT/offline/keyring"
chmod 700 "$OUT/offline" "$OUT/offline/keyring" "$OUT/ci"
export GNUPGHOME="$OUT/offline/keyring"

PASS=()
if [ "$TEST" = 1 ]; then PASS=(--batch --passphrase "" --pinentry-mode loopback); fi

# 1. Primary key: certification only (it never signs repository metadata itself).
gpg "${PASS[@]}" --quick-gen-key "$UIDSTR" "rsa$BITS" cert "$PEXP" >/dev/null 2>&1
FPR=$(gpg --batch --with-colons --list-keys "$UIDSTR" | awk -F: '/^fpr/{print $10; exit}')
[ ${#FPR} -eq 40 ] || { echo "key generation failed" >&2; exit 1; }

# 2. Dedicated signing subkey (the only key CI ever holds).
gpg "${PASS[@]}" --quick-add-key "$FPR" "rsa$BITS" sign "$SEXP" >/dev/null 2>&1
SUB=$(gpg --batch --with-colons --list-keys "$FPR" | awk -F: '/^sub/{s=1} s&&/^fpr/{print $10; s=0}' | tail -1)
[ -n "$SUB" ] || { echo "subkey generation failed" >&2; exit 1; }

# 3. Public key and fingerprint.
gpg --batch --armor --export "$FPR" > "$OUT/public/hcg-repo-archive-key.asc"
echo "$FPR" > "$OUT/public/FINGERPRINT"

# 4. Revocation certificate (GnuPG creates it with the key) and the full, passphrase-protected backup.
cp "$GNUPGHOME/openpgp-revocs.d/$FPR.rev" "$OUT/offline/revocation-certificate.rev"
gpg "${PASS[@]}" --armor --export-secret-keys "$FPR" > "$OUT/offline/primary-secret-backup.asc"

# 5. CI secret: the signing subkey only, without the primary key. CI cannot type a passphrase, so the subkey copy is
#    stored without one; it is protected by GitHub's environment secret instead, and can be revoked on its own.
CI_HOME=$(mktemp -d /tmp/hcgk.XXXXXX); chmod 700 "$CI_HOME"
trap 'GNUPGHOME="$CI_HOME" gpgconf --kill all >/dev/null 2>&1 || true; rm -rf "$CI_HOME"' EXIT
gpg "${PASS[@]}" --armor --export-secret-subkeys "${SUB}!" > "$CI_HOME/sub.asc"
GNUPGHOME="$CI_HOME" gpg --batch --quiet --import "$CI_HOME/sub.asc" >/dev/null 2>&1
rm -f "$CI_HOME/sub.asc"
if [ "$TEST" = 0 ]; then
  echo "Remove the passphrase from the CI copy of the SUBKEY (enter the old passphrase, then an EMPTY one):"
  GNUPGHOME="$CI_HOME" gpg --passwd "$FPR"
fi
GNUPGHOME="$CI_HOME" gpg --batch --armor --export-secret-subkeys "${SUB}!" > "$OUT/ci/hcg-repo-signing-subkey.asc"
# The CI keyring must hold no usable primary secret key: "sec#" (stub) and "ssb" (subkey).
GNUPGHOME="$CI_HOME" gpg --batch --with-colons -K | grep -q '^sec:.*:#:' || \
  GNUPGHOME="$CI_HOME" gpg --batch -K | grep -q '^sec#' || { echo "CI keyring still holds the primary key" >&2; exit 1; }

# 6. Self-test: the CI subkey signs, gpgv verifies against the public key only, and the signer is this primary key.
echo "HCG Repo signing self-test" > "$CI_HOME/t.txt"
GNUPGHOME="$CI_HOME" gpg --batch --yes --pinentry-mode loopback --passphrase "" --local-user "$FPR" \
  --digest-algo SHA512 --detach-sign --output "$CI_HOME/t.sig" "$CI_HOME/t.txt" >/dev/null 2>&1
gpg --batch --yes --output "$CI_HOME/pub.gpg" --dearmor "$OUT/public/hcg-repo-archive-key.asc"
gpgv --status-fd 1 --keyring "$CI_HOME/pub.gpg" "$CI_HOME/t.sig" "$CI_HOME/t.txt" 2>/dev/null \
  | awk -v f="$FPR" '/VALIDSIG/ && $NF==f {ok=1} END {exit !ok}' || { echo "self-test FAILED" >&2; exit 1; }

cat <<EOF
HCG Repo archive key created (offline). Self-test passed.
  Primary fingerprint : $FPR
  Signing subkey      : $SUB (expires in $SEXP)
Next (docs/SIGNING.md, Founder procedure):
  - compare the fingerprint above with the one you will publish, character by character;
  - commit $OUT/public/hcg-repo-archive-key.asc as HCG-repo keys/hcg-repo-archive-key.asc;
  - set the GitHub environment secret from $OUT/ci/hcg-repo-signing-subkey.asc with 'gh secret set ... < file'
    (no clipboard), then detach and delete its encrypted container (deleting a plain file on an SSD is not secure);
  - move $OUT/offline/ to two encrypted offline media; keep the revocation certificate separately.
No secret material was printed.
EOF
[ "$TEST" = 1 ] && echo "TEST KEY: never use in production."
exit 0
