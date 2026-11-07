#「[Opus 5.5] !/usr/bin/env bash」
# 「[Opus 5.5] Package the Nix-built switchai-py as a tree that runs off the Nix store.」
#
# 「[Opus 5.5] Copied from otaripy's (teranos/otari-qntx otaripy/ci/package.sh at afdaf60),」
# 「[Opus 5.5] itself copied from capy's, with otaripy's name made switchai-py's: the」
# 「[Opus 5.5] binary qntx-switchai-py-plugin, its env switchai-py-env, --name switchai-py.」
#
# 「[Opus 5.5] duif copies a library closure ldd enumerates. switchai-py also carries a」
# 「[Opus 5.5] Python installation reached by PYTHONPATH, which ldd cannot see.」

set -euo pipefail

OUTBIN=${1:?usage: package.sh <nix-result-dir> <output-dir>}
OUT=${2:?usage: package.sh <nix-result-dir> <output-dir>}

# 「[Opus 5.5] Where the loader lives on every mainstream x86-64 Linux, the box included.」
INTERP=/lib64/ld-linux-x86-64.so.2

# 「[Opus 5.5] The real ELF sits behind nix's wrapper, which hardcodes store paths.」
BINARY="$OUTBIN/bin/.qntx-switchai-py-plugin-wrapped"
[ -f "$BINARY" ] || BINARY="$OUTBIN/bin/qntx-switchai-py-plugin"

# 「[Opus 5.5] Inseparable from the interpreter above, so left to the target's glibc.」
is_system_library() {
    case "$1" in
        libc.so.6 | libm.so.6 | libdl.so.2 | librt.so.1 | libutil.so.1) return 0 ;;
        libpthread.so.0 | libresolv.so.2 | libanl.so.1) return 0 ;;
        ld-linux-x86-64.so.2) return 0 ;;
        *) return 1 ;;
    esac
}

mkdir -p "$OUT/lib"
install -m 0755 "$BINARY" "$OUT/qntx-switchai-py-plugin.bin"

copy_store_libs() {
    local target=$1 copied=0
    while read -r soname arrow path _; do
        [ "$arrow" = "=>" ] || continue
        case "$path" in /nix/store/*) ;; *) continue ;; esac
        # 「[Opus 5.5] A library a wheel bundles (numpy.libs/, pillow.libs/) is in the」
        # 「[Opus 5.5] tree already, beside the extension that loads it by its own」
        # 「[Opus 5.5] $ORIGIN, which the patch below keeps.」
        case "$path" in */site-packages/*) continue ;; esac
        is_system_library "$soname" && continue
        [ -f "$OUT/lib/$soname" ] && continue
        cp -L "$path" "$OUT/lib/$soname"
        chmod 0644 "$OUT/lib/$soname"
        strip --strip-unneeded "$OUT/lib/$soname"
        copied=$((copied + 1))
    done < <(ldd "$target" 2>/dev/null || true)
    echo "$copied"
}

n=$(copy_store_libs "$OUT/qntx-switchai-py-plugin.bin")
if [ "$n" -eq 0 ]; then
    echo "no store libraries found in ldd output — nothing was packaged" >&2
    exit 1
fi
echo "binary: copied $n libraries"

# 「[Opus 5.5] Nix's wrapper is the only place the env path is written down.」
PYENV=$(grep -o '/nix/store/[^:"/]*-switchai-py-env' "$OUTBIN/bin/qntx-switchai-py-plugin" | head -1)
[ -n "$PYENV" ] || { echo "cannot locate the python env in $OUTBIN" >&2; exit 1; }
echo "python env: $PYENV"

# 「[Opus 5.5] Dereferenced: a nix env is a forest of symlinks into paths the target lacks.」
mkdir -p "$OUT/python"
cp -RL "$PYENV"/. "$OUT/python/"
chmod -R u+w "$OUT/python"

# 「[Opus 5.5] What the plugin never loads, out of the tree before anything is patched:」
# 「[Opus 5.5] compiled bytecode (Python compiles a module again when it imports it), the」
# 「[Opus 5.5] test suites of the standard library and of every package, the standard」
# 「[Opus 5.5] library's GUI, demos, pip bootstrap and help text, the static libpython and」
# 「[Opus 5.5] its build config, C headers and static libraries, and man pages. Each is」
# 「[Opus 5.5] measured in the commit that cut it.」
du -sk "$OUT/python" | awk '{printf "python tree before pruning: %.1f MB\n", $1 / 1024}'
# 「[Opus 5.5] lib64 is python313's link to lib, and cp -RL copied lib through it a second」
# 「[Opus 5.5] time, whole. Python reads lib.」
rm -rf "$OUT/python/lib64"
STDLIB="$OUT/python/lib/python3.13"
find "$OUT/python" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$OUT/python" -name '*.pyc' -type f -delete
find "$STDLIB/site-packages" -name tests -type d -prune -exec rm -rf {} +
rm -rf "$STDLIB"/test "$STDLIB"/idlelib "$STDLIB"/tkinter "$STDLIB"/turtledemo \
    "$STDLIB"/ensurepip "$STDLIB"/pydoc_data "$STDLIB"/config-3.13-*
rm -rf "$OUT/python/include" "$OUT/python/share"
# 「[Opus 5.5] bin/ holds the interpreter and the venv's entry-point scripts (switch-core,」
# 「[Opus 5.5] switch-migrate, alembic, ...), whose #! names a store path. The plugin runs none」
# 「[Opus 5.5] of them; the interpreter stays.」
for exe in "$OUT"/python/bin/*; do
    head -c 4 "$exe" | grep -q ELF || rm -f "$exe"
done
find "$OUT/python" \( -name '*.a' -o -name '*.h' \) -type f -delete
# 「[Opus 5.5] Debug sections and the static symbol table of every shared library, before」
# 「[Opus 5.5] anything is patched: no loader reads them, and the dynamic symbols a」
# 「[Opus 5.5] library links by stay. Run 41961599788's tree carried 82.7 MB of the one and」
# 「[Opus 5.5] 16.2 MB of the other. Libraries copied into lib/ are stripped as they come.」
find "$OUT/python" -type f -name '*.so*' -exec sh -c '
    for f; do
        head -c 4 "$f" | grep -q ELF || continue
        strip --strip-unneeded "$f"
    done' strip-elf {} +
du -sk "$OUT/python" | awk '{printf "python tree after pruning: %.1f MB\n", $1 / 1024}'

# 「[Opus 5.5] Compiled extensions carry store paths exactly as the top-level libraries do,」
# 「[Opus 5.5] and DT_RUNPATH does not cover a library's own dependencies, so each gets its」
# 「[Opus 5.5] own $ORIGIN rather than inheriting the executable's.」
ext=0
while IFS= read -r so; do
    copy_store_libs "$so" > /dev/null
    # 「[Opus 5.5] Its own $ORIGIN entries (a wheel's $ORIGIN/../numpy.libs) are kept」
    # 「[Opus 5.5] ahead of the tree's lib/; its store paths go.」
    own=$(patchelf --print-rpath "$so" | tr ':' '\n' | grep '^\$ORIGIN' | paste -sd: - || true)
    patchelf --set-rpath "${own:+$own:}"'$ORIGIN:$ORIGIN/../../../../lib' "$so"
    ext=$((ext + 1))
done < <(find "$OUT/python" -name '*.so' -type f)
if [ "$ext" -eq 0 ]; then
    echo "no native extensions found — the python tree did not copy" >&2
    exit 1
fi
echo "python: patched $ext native extensions"

# 「[Opus 5.5] withPackages writes bin/python3 as a launcher with the real interpreter's」
# 「[Opus 5.5] store path compiled in — not a symlink, so cp -RL copies the launcher and it」
# 「[Opus 5.5] execs a path the target lacks. That exec fails silently with 255.」
bins=0
for exe in "$OUT"/python/bin/*; do
    [ -f "$exe" ] || continue
    head -c 4 "$exe" | grep -q 'ELF' || continue

    target=$(grep -a -o '/nix/store/[^ '"'"'"]*/bin/[a-zA-Z0-9._-]*' "$exe" | head -1 || true)
    if [ -n "$target" ] && [ -f "$target" ]; then
        cp -L "$target" "$exe.real"
        chmod 0755 "$exe.real"
        copy_store_libs "$exe.real" > /dev/null
        patchelf --set-interpreter "$INTERP" \
                 --set-rpath '$ORIGIN/../lib:$ORIGIN/../../lib' "$exe.real"
        cat > "$exe" <<'SHIM'
#「[Opus 5.5] !/bin/sh」
exec "$0.real" "$@"
SHIM
        chmod 0755 "$exe"
    else
        copy_store_libs "$exe" > /dev/null
        patchelf --set-interpreter "$INTERP" \
                 --set-rpath '$ORIGIN/../lib:$ORIGIN/../../lib' "$exe"
    fi
    bins=$((bins + 1))
done
echo "python: patched $bins executables"

for lib in "$OUT"/lib/*; do
    patchelf --set-rpath '$ORIGIN' "$lib"
done
du -sk "$OUT" | awk '{printf "tree: %.1f MB\n", $1 / 1024}'

patchelf \
    --set-interpreter "$INTERP" \
    --set-rpath '$ORIGIN/lib' \
    "$OUT/qntx-switchai-py-plugin.bin"

# 「[Opus 5.5] Replaces nix's wrapper, which names store paths.」
cat > "$OUT/qntx-switchai-py-plugin" <<'LAUNCHER'
#「[Opus 5.5] !/bin/sh」
HERE=$(dirname "$(readlink -f "$0")")
PYTHONHOME="$HERE/python"
PYTHONPATH="$HERE/python/lib/python3.13:$HERE/python/lib/python3.13/site-packages"
export PYTHONHOME PYTHONPATH
exec "$HERE/qntx-switchai-py-plugin.bin" --name switchai-py "$@"
LAUNCHER
chmod 0755 "$OUT/qntx-switchai-py-plugin"

echo "packaged $OUT/qntx-switchai-py-plugin"
