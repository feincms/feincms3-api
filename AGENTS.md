# Agent notes

## Tests

- Quick run with the local virtualenv:
  `.venv/bin/python tests/manage.py test testapp`
- Full matrix: `tox` (environments are listed in `tox.ini`). CI
  (`.github/workflows/tests.yml`) runs every `py<version>-*` environment for
  each Python version in its matrix, so keep the CI matrix, the tox envlist
  and the `pyproject.toml` classifiers in sync.
- The test app (`tests/testapp/`) has no migrations; tables are created
  directly from the models.

## Layout

- `specs/api.md`: the design and the reasoning behind it. Read it before
  working on the API, and keep it up to date when decisions change. It
  describes what is, not a backlog: plans and ideas are GitHub issues
  (`gh issue create`), linked from the spec where it helps.
- `docs/architecture.md`: how the code is organised, module by module, and
  where projects plug in. Keep it up to date when modules or their roles
  change.
- `feincms3_api/`: the API (django-ninja), the main purpose of the package.
- `tests/testapp/snapshot.txt`: the `f3api_schema` output for the test app;
  regenerate it with `.venv/bin/python tests/manage.py f3api_schema
  testapp.content_api.content_api > tests/testapp/snapshot.txt` and look at
  the diff.
- `cli/`: `feincms3-api-cli` (command `f3api`, also `feincms3-api-cli` for `uvx`), a separate package with its
  own `pyproject.toml`, standard library only, talking HTTP. Its tests are
  `tests/testapp/test_cli.py` (against the live test server), so they run
  with the package's.

## Conventions

- Formatting and linting: `ruff` and `pyproject-fmt` via `prek`
  (`prek install` once per checkout, `prek run --all-files`).
- Add user-facing changes to the "Next version" section of `CHANGELOG.md`.

## Commits

- Commit feature by feature: one commit per fix or feature, each including
  its tests and changelog entry.
- No AI attribution lines. Commits which move or rework code from
  feincms3-autotranslate credit its author with
  `Co-authored-by: Moritz Wettstein <mw@feinheit.ch>`.
- Push in batches: commit locally as you go, push to `origin`
  (github.com/feincms/feincms3-api) after a batch of related commits, not
  after every commit, and always with the tests passing.
- Keep commit messages short and don't repeat what the diff or changelog
  already says; a concise subject line is usually enough.

## Releases

- `.github/workflows/publish.yml` publishes on tags: `1.2.3` publishes
  feincms3-api, `cli-1.2.3` feincms3-api-cli (from `cli/`). The tag has to
  match the package's `__version__` (`0.1` matches `0.1.0`).
- The maintainer releases with `make-release` (in `cli/` for the CLI, which
  has its own `CHANGELOG.md`): it dates the changelog, bumps the version,
  commits, tags and pushes.
- The README is the PyPI description: link to files in the repository with
  absolute GitHub URLs.
