#!/bin/sh
# Trim the uv-managed runtime for production images: remove tooling the app never
# uses at runtime — pip (scripts AND package), idle, pydoc, C headers,
# pkg-config, tkinter/tcl, tests — plus the uv/uvx build binaries themselves.
# We build and manage everything with uv, so none of this is needed to *run*.
#
# Baked into the shared python-uv image and invoked as `RUN strip-python` by the
# backend/pymta distroless builds and the slim runtime-prod stages, so the list
# lives in exactly one place.
#
# NOTE: this shrinks the running-container attack surface (files are whited-out),
# but it only shrinks image *size* for the distroless targets, which COPY an
# already-trimmed tree into a clean base. For the slim runtime-prod images the
# Python/uv sit in an inherited layer, so the bytes remain in the pull.
#
# Version-agnostic: the interpreter path comes from `uv python find`; stdlib
# paths glob python3.*. uv/uvx are removed LAST, after uv is used above.
set -eu

PYDIR=$(dirname "$(dirname "$(uv python find)")")

# shellcheck disable=SC2086  # deliberate globbing of the paths below
rm -rf \
    "$PYDIR"/bin/idle* "$PYDIR"/bin/pip* "$PYDIR"/bin/pydoc* "$PYDIR"/bin/*-config \
    "$PYDIR"/include "$PYDIR"/share \
    "$PYDIR"/lib/pkgconfig "$PYDIR"/lib/itcl* "$PYDIR"/lib/libtcl* \
    "$PYDIR"/lib/tcl* "$PYDIR"/lib/tk* "$PYDIR"/lib/thread* \
    "$PYDIR"/lib/python3.*/idlelib \
    "$PYDIR"/lib/python3.*/ensurepip \
    "$PYDIR"/lib/python3.*/tkinter \
    "$PYDIR"/lib/python3.*/turtledemo \
    "$PYDIR"/lib/python3.*/lib-dynload/_tkinter* \
    "$PYDIR"/lib/python3.*/lib-dynload/_ctypes_test* \
    "$PYDIR"/lib/python3.*/site-packages/pip "$PYDIR"/lib/python3.*/site-packages/pip-*.dist-info \
    "${UV_PYTHON_INSTALL_DIR:-/opt/python}"/.gitignore \
    "${UV_PYTHON_INSTALL_DIR:-/opt/python}"/.lock \
    "${UV_PYTHON_INSTALL_DIR:-/opt/python}"/.temp

# uv/uvx are build tools — never needed to run the app. Remove after the
# `uv python find` above.
rm -f /bin/uv /bin/uvx
