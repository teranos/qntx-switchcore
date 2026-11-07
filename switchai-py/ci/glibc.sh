#「[Opus 5.5] !/usr/bin/env bash」
# 「[Opus 5.5] Fail when any ELF in a tree asks for a GLIBC symbol version above the」
# 「[Opus 5.5] target's C library, naming the file and the version it asks for.」
#
# 「[Opus 5.5] usage: glibc.sh <tree> <target glibc, e.g. 2.41> [readelf]」
#
# 「[Opus 5.5] The tree carries no C library of its own (package.sh leaves libc, libm, the」
# 「[Opus 5.5] loader and the rest to the target), so every ELF in it runs on the target's,」
# 「[Opus 5.5] and one asking for a newer version does not load there: math's extension」
# 「[Opus 5.5] asked for GLIBC_2.44 on the Debian image in run 41958584572.」

set -euo pipefail

TREE=${1:?usage: glibc.sh <tree> <target glibc> [readelf]}
TARGET=${2:?usage: glibc.sh <tree> <target glibc> [readelf]}
READELF=${3:-readelf}

# 「[Opus 5.5] Whether version $1 is above $2, both as N.N.」
above() {
    [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n 1)" = "$1" ]
}

checked=0
too_new=0
while IFS= read -r -d '' file; do
    head -c 4 "$file" | grep -q 'ELF' || continue
    checked=$((checked + 1))
    # 「[Opus 5.5] The versions it needs: the GLIBC_ entries of its version-needs section.」
    if ! versions=$("$READELF" -V --wide "$file" 2>&1); then
        echo "${file#"$TREE"/} could not be read: $versions" >&2
        too_new=$((too_new + 1))
        continue
    fi
    asked=$(printf '%s\n' "$versions" \
        | sed -n 's/.*Name: GLIBC_\([0-9][0-9.]*\).*/\1/p' | sort -uV | awk 'END {print}')
    [ -n "$asked" ] || continue
    if above "$asked" "$TARGET"; then
        echo "${file#"$TREE"/} asks for GLIBC_$asked, above the target's $TARGET" >&2
        too_new=$((too_new + 1))
    fi
done < <(find "$TREE" -type f -print0)

echo "glibc: $checked ELF files read, $too_new unreadable or asking for more than GLIBC_$TARGET"
[ "$too_new" -eq 0 ]
