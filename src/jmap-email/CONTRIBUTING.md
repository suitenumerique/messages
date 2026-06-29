# Contributing to `jmap-email`

Thanks for considering a contribution. This package is small and
focused; the bar for accepting changes is that they make the library
more correct, more spec-conformant, or better-documented without
adding runtime dependencies.

## Development environment

Two paths are supported.

### Docker (matches CI)

From the repository root:

```bash
make test-jmap-email        # full test suite
make typecheck-jmap-email   # ty (Astral)
```

These spin up the same container image CI uses, so the only divergence
between local results and CI is the host architecture (arm64 vs x86_64).

### Native Python 3.14.5+

```bash
cd src/jmap-email
pip install -e '.[dev]'

pytest                       # default selection — fuzz tests excluded
pytest -m fuzz               # property-based / Hypothesis fuzz tests
ruff check .
ruff format --check .
```

## Pull-request checklist

Every PR should:

- Add or update a test that fails without the change and passes with
  it. For parser fixes, the test goes in `tests/test_parser.py` near
  the closest existing class; for composer fixes,
  `tests/test_composer.py`; for shape-helper fixes,
  `tests/test_helpers.py`.
- Keep `make typecheck-jmap-email` green. `ty` is the source of truth
  for type contracts.
- Not introduce a runtime dependency. The package's value comes
  from being a clean stdlib wrapper; new deps are rejected unless they
  ship a CVE fix the stdlib won't.
- Update `CHANGELOG.md` under the `Unreleased` heading when the change
  is user-visible.
- Update `README.md` when public API surface, conformance status, or
  resource defaults move.

## Coding conventions

- **PEP 585 / PEP 604 typing.** Use `list[X]` / `dict[K, V]` /
  `X | None` rather than `typing.List[X]` etc. No `from __future__
  import annotations` — the supported floor is 3.14.5.
- **No legacy stdlib imports inside hot paths.** If a regex or
  `email.utils` helper is the wrong tool, write the loop.
- **Module-private symbols** are prefixed with `_`. Anything in
  `__all__` is part of the wire contract — changes that rename or
  remove a name require a major-version bump (post-1.0) or a clear
  `CHANGELOG` Removed entry (during 0.x).

## Adding a regression test for a new CVE / paper

1. Add the test to the appropriate `tests/` module under the
   `TestParserSecurityRegressions` or `TestComposerRFCAudit` class
   (whichever fits).
2. Reference the CVE / paper by id in the test docstring.
3. Add the entry to the [defense matrix](README.md#defense-matrix) in
   the README.

## Security-sensitive changes

See `SECURITY.md`. Don't open a public PR or issue for a vulnerability
before coordinating disclosure.
