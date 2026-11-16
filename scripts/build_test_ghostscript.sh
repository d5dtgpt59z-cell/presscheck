#!/usr/bin/env bash
# CI-only Linux engine: exact official source, local libraries and ROM resources.
# The copied CMYK profile is a TEST FIXTURE, never a printer-approved default.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GS_VERSION="10.08.0"
GS_SOURCE_ROOT="ghostscript-10.08.0"
GS_URL="https://github.com/ArtifexSoftware/ghostpdl-downloads/releases/download/gs10080/ghostscript-10.08.0.tar.xz"
GS_SHA256="c20492bc8ebb96c87fa2e52a0926e1cda8cde95d66145e018ac713fed5da38cf"
GS_CACHE_ROOT="$ROOT_DIR/work/ghostscript-ci"
GS_PREFIX="${1:-$GS_CACHE_ROOT/runtime}"
GS_MACHINE="$(uname -m)"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This CI recipe builds natively on Linux; use Magdobe's native recipe on macOS." >&2
  exit 1
fi
GS_RECIPE_SHA256="$(sha256sum "${BASH_SOURCE[0]}" | cut -d ' ' -f 1)"
case "$GS_MACHINE" in x86_64|aarch64) ;; *) echo "Unsupported native CI architecture: $GS_MACHINE" >&2; exit 1 ;; esac
mkdir -p "$GS_CACHE_ROOT"

# A cache hit is still checked against source identity, recipe and actual bytes
# before the executable is launched. Cache keys also include OS and architecture.
verify_cache() {
  python3 - "$GS_PREFIX" "$GS_VERSION" "$GS_SHA256" "$GS_RECIPE_SHA256" "$GS_MACHINE" <<'PY'
import hashlib, json, pathlib, sys
prefix, version, source, recipe, architecture = sys.argv[1:]
prefix = pathlib.Path(prefix)
record = json.loads((prefix / "build.json").read_text())
assert record["version"] == version and record["source_sha256"] == source
assert record["recipe_sha256"] == recipe and record["architecture"] == architecture
for name, expected in record["files"].items():
    assert name in ("bin/gs", "fixtures/test-only-default-cmyk.icc")
    data = (prefix / name).read_bytes()
    assert hashlib.sha256(data).hexdigest() == expected, "Cached engine/fixture bytes changed"
assert set(record["files"]) == {"bin/gs", "fixtures/test-only-default-cmyk.icc"}
profile = (prefix / "fixtures/test-only-default-cmyk.icc").read_bytes()
assert 128 <= len(profile) <= 16*1024*1024
assert profile[12:16] == b"prtr" and profile[16:20] == b"CMYK" and profile[36:40] == b"acsp"
PY
}

if [[ -e "$GS_PREFIX" ]]; then
  verify_cache
else
  mkdir -p "$GS_CACHE_ROOT/source"
  GS_ARCHIVE="$GS_CACHE_ROOT/source/$GS_SOURCE_ROOT.tar.xz"
  if [[ ! -f "$GS_ARCHIVE" ]]; then
    curl --disable --fail --location --silent --show-error --proto '=https' --proto-redir '=https' \
      --retry 3 --connect-timeout 20 --max-time 600 --output "$GS_ARCHIVE.download" "$GS_URL"
    printf '%s  %s\n' "$GS_SHA256" "$GS_ARCHIVE.download" | sha256sum --check --status
    mv "$GS_ARCHIVE.download" "$GS_ARCHIVE"
  fi
  # No archive is extracted and no upstream build script is executed before this.
  printf '%s  %s\n' "$GS_SHA256" "$GS_ARCHIVE" | sha256sum --check --status
  GS_WORK="$(mktemp -d "$GS_CACHE_ROOT/build.XXXXXX")"
  tar --no-same-owner --no-same-permissions -xJf "$GS_ARCHIVE" -C "$GS_WORK"
  GS_SOURCE="$GS_WORK/$GS_SOURCE_ROOT"
  GS_STAGE="$GS_WORK/runtime"
  mkdir -p "$GS_STAGE/bin" "$GS_STAGE/fixtures"
  GS_OPTIONS=(--prefix=/presscheck/ghostscript-ci --without-x --disable-cups --disable-fontconfig
    --disable-dbus --disable-gtk --without-tesseract --without-libidn --without-libpaper
    --without-ijs --without-urf --without-so --without-cal --without-pdftoraster
    --enable-compile-inits --enable-mkromfs-quiet --with-fontpath=
    --with-drivers=pdfwrite,png16m,pngalpha,inkcov,txtwrite)
  if ! (cd "$GS_SOURCE" && env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=C LC_ALL=C \
      CC=cc CXX=c++ CFLAGS=-O2 CXXFLAGS=-O2 PKG_CONFIG=/usr/bin/false \
      ./configure "${GS_OPTIONS[@]}" > "$GS_WORK/configure.log" 2>&1); then
    tail -n 100 "$GS_WORK/configure.log" >&2; exit 1
  fi
  if ! (cd "$GS_SOURCE" && env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=C LC_ALL=C \
      PKG_CONFIG=/usr/bin/false /usr/bin/make -j4 > "$GS_WORK/build.log" 2>&1); then
    tail -n 100 "$GS_WORK/build.log" >&2; exit 1
  fi
  install -m 0755 "$GS_SOURCE/bin/gs" "$GS_STAGE/bin/gs"
  install -m 0644 "$GS_SOURCE/iccprofiles/default_cmyk.icc" "$GS_STAGE/fixtures/test-only-default-cmyk.icc"
  printf '%s\n' 'TEST ONLY: Ghostscript upstream default CMYK profile. Not a supplier-approved printing condition or application default.' > "$GS_STAGE/fixtures/README.txt"
  python3 - "$GS_STAGE" "$GS_VERSION" "$GS_SHA256" "$GS_RECIPE_SHA256" "$GS_MACHINE" "$GS_URL" <<'PY'
import hashlib, json, pathlib, sys
prefix, version, source, recipe, architecture, url = sys.argv[1:]
prefix = pathlib.Path(prefix)
files = {name: hashlib.sha256((prefix / name).read_bytes()).hexdigest()
         for name in ("bin/gs", "fixtures/test-only-default-cmyk.icc")}
(prefix / "build.json").write_text(json.dumps({"version": version, "source_url": url,
    "source_sha256": source, "recipe_sha256": recipe, "architecture": architecture,
    "resources": "ROM embedded", "files": files}, indent=2) + "\n")
PY
  mkdir -p "$(dirname "$GS_PREFIX")"
  mv "$GS_STAGE" "$GS_PREFIX"
  verify_cache
fi

GS_EXECUTABLE="$(cd "$GS_PREFIX/bin" && pwd)/gs"
GS_TEST_ICC="$(cd "$GS_PREFIX/fixtures" && pwd)/test-only-default-cmyk.icc"
GS_ACTUAL_VERSION="$(env -i PATH=/usr/bin:/bin LANG=C "$GS_EXECUTABLE" --version)"
[[ "$GS_ACTUAL_VERSION" == "$GS_VERSION" ]] || { echo "Unexpected cached Ghostscript version" >&2; exit 1; }
env -i PATH=/usr/bin:/bin LANG=C "$GS_EXECUTABLE" -q -dSAFER -dBATCH -dNOPAUSE -sDEVICE=nullpage \
  -c '(%rom%Resource/Init/gs_init.ps) (r) file closefile (%rom%iccprofiles/default_rgb.icc) (r) file closefile /Helvetica findfont pop quit'
if [[ -n "${GITHUB_ENV:-}" ]]; then
  printf 'MAGDOBE_GHOSTSCRIPT=%s\nMAGDOBE_TEST_ICC=%s\n' "$GS_EXECUTABLE" "$GS_TEST_ICC" >> "$GITHUB_ENV"
fi
printf 'Verified Ghostscript %s (%s) with ROM resources.\nMAGDOBE_GHOSTSCRIPT=%s\nMAGDOBE_TEST_ICC=%s\n' "$GS_VERSION" "$GS_MACHINE" "$GS_EXECUTABLE" "$GS_TEST_ICC"
