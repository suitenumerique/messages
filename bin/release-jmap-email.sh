#!/usr/bin/env bash
#
# Interactive PyPI release for the jmap-email package.
#
# Hermetic: every step runs inside the python:3.14.5-slim image, so a
# clean VM only needs Docker. The host never touches pip or twine.
#
# Flow:
#   1. Run ``make lint-jmap-email typecheck-jmap-email test-jmap-email``
#   2. Build sdist + wheel inside Docker, run ``twine check``
#   3. Auto-inspect wheel/sdist contents and METADATA fields
#   4. Prompt for TestPyPI API token, upload, smoke-install in a
#      throwaway container
#   5. Prompt for PyPI API token, upload
#
# Each gate is interactive (y/N). Bail out anytime with Ctrl-C.
#
# Set ``SKIP_GATES=1`` to skip lint/typecheck/tests on retry.

set -eo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG_DIR="${REPO_DIR}/src/jmap-email"
PYTHON_IMAGE="python:3.14.5-slim"

BOLD=$'\033[1m'
GREEN=$'\033[1;32m'
BLUE=$'\033[1;34m'
RED=$'\033[1;31m'
YELLOW=$'\033[1;33m'
RESET=$'\033[0m'

say()   { printf '%s%s%s\n' "${BLUE}" "$*" "${RESET}"; }
ok()    { printf '%s✓ %s%s\n' "${GREEN}" "$*" "${RESET}"; }
warn()  { printf '%s%s%s\n' "${YELLOW}" "$*" "${RESET}"; }
die()   { printf '%s✗ %s%s\n' "${RED}" "$*" "${RESET}" >&2; exit 1; }

confirm() {
    local prompt="$1"
    local ans
    read -r -p "${BOLD}${prompt} [y/N]${RESET} " ans
    [[ "${ans}" =~ ^[Yy]$ ]] || die "aborted"
}

read_token() {
    # $1 = label, $2 = name of variable to assign into
    local label="$1" varname="$2" token
    read -r -s -p "${BOLD}${label} API token (pypi-…):${RESET} " token
    echo
    [[ -n "${token}" ]] || die "empty token"
    [[ "${token}" == pypi-* ]] || warn "token does not start with 'pypi-' — continuing anyway"
    printf -v "${varname}" '%s' "${token}"
}

# ── pre-flight ────────────────────────────────────────────────────────────
command -v docker >/dev/null || die "docker not found in PATH"
[[ -f "${PKG_DIR}/pyproject.toml" ]] || die "no pyproject.toml at ${PKG_DIR}"

VERSION="$(awk -F'"' '/^version = /{print $2; exit}' "${PKG_DIR}/pyproject.toml")"
[[ -n "${VERSION}" ]] || die "could not read version from pyproject.toml"

printf '\n%s════════════════════════════════════════════════════════════%s\n' "${BLUE}" "${RESET}"
printf '%s  Release jmap-email %s%s\n' "${BLUE}" "${VERSION}" "${RESET}"
printf '%s════════════════════════════════════════════════════════════%s\n\n' "${BLUE}" "${RESET}"
say "Image:      ${PYTHON_IMAGE}"
say "Package:    ${PKG_DIR}"
say "Flow:       lint+typecheck+tests → build → inspect → TestPyPI → smoke install → PyPI"
[[ "${SKIP_GATES:-0}" == "1" ]] && warn "SKIP_GATES=1 — lint/typecheck/tests will be skipped"
echo
confirm "Proceed?"

# ── 1. lint, typecheck, tests ─────────────────────────────────────────────
if [[ "${SKIP_GATES:-0}" == "1" ]]; then
    warn "skipping lint/typecheck/tests (SKIP_GATES=1)"
else
    say "→ make lint-jmap-email"
    make -C "${REPO_DIR}" lint-jmap-email

    say "→ make typecheck-jmap-email"
    make -C "${REPO_DIR}" typecheck-jmap-email

    say "→ make test-jmap-email"
    make -C "${REPO_DIR}" test-jmap-email

    ok "Lint + typecheck + tests passed"
fi

# ── 2. build + check ──────────────────────────────────────────────────────
say "→ Cleaning previous artifacts"
rm -rf "${PKG_DIR}/dist" "${PKG_DIR}/build"

say "→ Building sdist + wheel inside ${PYTHON_IMAGE}"
docker run --rm -t \
    --user "$(id -u):$(id -g)" \
    -v "${PKG_DIR}:/pkg" \
    -w /pkg \
    -e HOME=/tmp \
    "${PYTHON_IMAGE}" \
    bash -c '
        set -eo pipefail
        pip install --quiet --no-cache-dir --root-user-action=ignore --target /tmp/pip build twine
        export PYTHONPATH=/tmp/pip
        python -m build --outdir dist
        python -m twine check dist/*
    '

echo
ls -lh "${PKG_DIR}/dist/"
echo
ok "Build + twine check passed"

# ── 3. inspect artifact contents ──────────────────────────────────────────
say "→ Inspecting wheel + sdist contents and METADATA"
docker run --rm -i \
    -v "${PKG_DIR}/dist:/dist:ro" \
    "${PYTHON_IMAGE}" \
    python - "${VERSION}" <<'PYEOF'
import re
import sys
import tarfile
import zipfile
from pathlib import Path

VERSION = sys.argv[1]
DIST = Path("/dist")
WHEEL = DIST / f"jmap_email-{VERSION}-py3-none-any.whl"
SDIST = DIST / f"jmap_email-{VERSION}.tar.gz"

errors, warnings = [], []

# ── wheel: file list ─────────────────────────────────────────────
if not WHEEL.exists():
    print(f"FATAL: wheel missing: {WHEEL}")
    sys.exit(1)
with zipfile.ZipFile(WHEEL) as zf:
    wheel_names = set(zf.namelist())
    metadata = zf.read(f"jmap_email-{VERSION}.dist-info/METADATA").decode()
    wheel_file_count = sum(1 for n in wheel_names if not n.endswith("/"))

expected_wheel = {
    "jmap_email/__init__.py",
    "jmap_email/composer.py",
    "jmap_email/helpers.py",
    "jmap_email/limits.py",
    "jmap_email/parser.py",
    "jmap_email/types.py",
    "jmap_email/py.typed",
    f"jmap_email-{VERSION}.dist-info/METADATA",
    f"jmap_email-{VERSION}.dist-info/WHEEL",
    f"jmap_email-{VERSION}.dist-info/RECORD",
}
missing = expected_wheel - wheel_names
if missing:
    errors.append(f"wheel missing required files: {sorted(missing)}")

forbidden = [
    ("tests/",       lambda n: n.startswith("tests/")),
    ("examples/",    lambda n: n.startswith("examples/")),
    (".pyc files",   lambda n: n.endswith(".pyc")),
    ("__pycache__",  lambda n: "__pycache__" in n),
    ("Dockerfile",   lambda n: n.endswith("Dockerfile")),
    (".pytest_cache",lambda n: ".pytest_cache" in n),
]
for label, pred in forbidden:
    bad = [n for n in wheel_names if pred(n)]
    if bad:
        errors.append(f"wheel contains forbidden {label}: {bad[:3]}")

if not any("LICENSE" in n for n in wheel_names):
    errors.append("wheel does not bundle LICENSE")

# ── wheel: METADATA ──────────────────────────────────────────────
def meta(key):
    m = re.search(rf"^{re.escape(key)}: (.+)$", metadata, re.MULTILINE)
    return m.group(1).strip() if m else None

if meta("Name") != "jmap-email":
    errors.append(f"METADATA Name: expected 'jmap-email', got {meta('Name')!r}")
if meta("Version") != VERSION:
    errors.append(f"METADATA Version: expected {VERSION!r}, got {meta('Version')!r}")

rp = meta("Requires-Python")
if not rp or "3.14" not in rp:
    errors.append(f"METADATA Requires-Python missing or not 3.14+: {rp!r}")

# Modern PEP 639 uses License-Expression; older hatchling emits License.
if not (meta("License-Expression") or meta("License")):
    errors.append("METADATA has no License or License-Expression")

dct = meta("Description-Content-Type")
if not dct or "markdown" not in dct.lower():
    errors.append(f"METADATA Description-Content-Type isn't markdown: {dct!r}")

if "# jmap-email" not in metadata:
    warnings.append("METADATA description doesn't contain '# jmap-email' — README may not have been embedded")

for url in ("Homepage", "Repository"):
    if f"Project-URL: {url}," not in metadata:
        warnings.append(f"METADATA missing Project-URL: {url}")

if "Topic :: Communications :: Email" not in metadata:
    warnings.append("METADATA missing 'Topic :: Communications :: Email' classifier")

# ── sdist ────────────────────────────────────────────────────────
if not SDIST.exists():
    print(f"FATAL: sdist missing: {SDIST}")
    sys.exit(1)
with tarfile.open(SDIST) as tf:
    sdist_names = tf.getnames()

prefix = f"jmap_email-{VERSION}/"
sdist_rel = {n[len(prefix):] for n in sdist_names if n.startswith(prefix)}

expected_sdist = {
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "PKG-INFO",
    "jmap_email/__init__.py",
    "jmap_email/parser.py",
    "jmap_email/composer.py",
    "jmap_email/py.typed",
}
missing_sdist = expected_sdist - sdist_rel
if missing_sdist:
    errors.append(f"sdist missing required files: {sorted(missing_sdist)}")

if not any(n.startswith("tests/") and n.endswith(".py") for n in sdist_rel):
    warnings.append("sdist contains no tests/*.py — pyproject.toml asked for tests/**/*.py")
if not any(n.startswith("examples/") and n.endswith(".py") for n in sdist_rel):
    warnings.append("sdist contains no examples/*.py — pyproject.toml asked for examples/**/*.py")

bad_sdist = [n for n in sdist_rel if n.endswith(".pyc") or "__pycache__" in n]
if bad_sdist:
    errors.append(f"sdist contains bytecode: {bad_sdist[:3]}")

# ── report ──────────────────────────────────────────────────────
print()
print(f"  Wheel:  {WHEEL.name}  ({WHEEL.stat().st_size // 1024} KB, {wheel_file_count} files)")
print(f"  Sdist:  {SDIST.name}  ({SDIST.stat().st_size // 1024} KB, {len(sdist_rel)} entries)")
print()
print("  METADATA highlights:")
for key in ("Name", "Version", "Requires-Python",
            "License-Expression", "License", "Description-Content-Type"):
    v = meta(key)
    if v:
        print(f"    {key:28s} {v}")
print()

if warnings:
    print("  ⚠  Warnings:")
    for w in warnings:
        print(f"     {w}")
    print()

if errors:
    print("  ✗ Errors:")
    for e in errors:
        print(f"     {e}")
    print()
    sys.exit(1)

print("  ✓ All artifact checks passed")
PYEOF

echo
confirm "Artifacts look right?"

# ── 4. TestPyPI ───────────────────────────────────────────────────────────
say "→ TestPyPI upload"
echo "Get a token at https://test.pypi.org/manage/account/token/"
echo "(Account-scoped on first release; project-scoped after.)"
read_token "TestPyPI" TESTPYPI_TOKEN

docker run --rm -t \
    --user "$(id -u):$(id -g)" \
    -v "${PKG_DIR}:/pkg" \
    -w /pkg \
    -e HOME=/tmp \
    -e TWINE_USERNAME=__token__ \
    -e TWINE_PASSWORD="${TESTPYPI_TOKEN}" \
    "${PYTHON_IMAGE}" \
    bash -c '
        set -eo pipefail
        pip install --quiet --no-cache-dir --root-user-action=ignore --target /tmp/pip twine
        export PYTHONPATH=/tmp/pip
        python -m twine upload --repository-url https://test.pypi.org/legacy/ dist/*
    '
ok "Uploaded to TestPyPI"
echo "  https://test.pypi.org/project/jmap-email/${VERSION}/"

# ── 5. smoke install ──────────────────────────────────────────────────────
say "→ Smoke-installing jmap-email==${VERSION} from TestPyPI"
# TestPyPI's index has limited transitive coverage; jmap-email has zero
# runtime deps so a bare TestPyPI install is fine. The retry loop covers
# the index-propagation lag (~30s after upload).
docker run --rm -t \
    "${PYTHON_IMAGE}" \
    bash -c "
        set -eo pipefail
        for i in 1 2 3 4 5; do
            if pip install --quiet --no-cache-dir \
                --index-url https://test.pypi.org/simple/ \
                jmap-email==${VERSION}; then
                break
            fi
            echo 'index not yet propagated, retrying in 10s…'
            sleep 10
        done
        python -c '
import jmap_email
assert jmap_email.__version__ == \"${VERSION}\", jmap_email.__version__
e = jmap_email.parse_email(b\"From: a@b\r\nSubject: t\r\n\r\nhi\")
assert e is not None and e[\"subject\"] == \"t\"
print(\"smoke-install ok — version\", jmap_email.__version__)
'
    "
ok "Smoke install passed"

# ── 6. real PyPI ──────────────────────────────────────────────────────────
echo
warn "Next step is irreversible: PyPI version numbers cannot be reused."
confirm "Publish jmap-email ${VERSION} to real PyPI?"

say "→ PyPI upload"
echo "Get a token at https://pypi.org/manage/account/token/"
read_token "PyPI" PYPI_TOKEN

docker run --rm -t \
    --user "$(id -u):$(id -g)" \
    -v "${PKG_DIR}:/pkg" \
    -w /pkg \
    -e HOME=/tmp \
    -e TWINE_USERNAME=__token__ \
    -e TWINE_PASSWORD="${PYPI_TOKEN}" \
    "${PYTHON_IMAGE}" \
    bash -c '
        set -eo pipefail
        pip install --quiet --no-cache-dir --root-user-action=ignore --target /tmp/pip twine
        export PYTHONPATH=/tmp/pip
        python -m twine upload dist/*
    '

echo
ok "jmap-email ${VERSION} released to PyPI"
echo "  https://pypi.org/project/jmap-email/${VERSION}/"
echo
echo "Next: tag the release in git when you're ready:"
echo "  git tag jmap-email-${VERSION} && git push origin jmap-email-${VERSION}"
