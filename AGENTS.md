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

- `specs/api.md`: the spec. Read it before working on the API, and keep it
  up to date when decisions change.
- `feincms3_api/`: the API (django-ninja), the main purpose of the package.
  `api.py` (`ContentAPI`: registration, put together from the operation
  mixins in `reading.py`, `schema.py` and `writing.py`), `content.py`
  (planning a content write), `http.py` (the ninja routes around the
  operations), `resources.py` (what's read from the admin), `adapters.py`
  (an object's or plugin's `fields`, binding them to admin forms),
  `codecs.py` (one form field in wire format and JSON schema), `errors.py`,
  `models.py`, `auth.py` and `admin.py` (API tokens), `cabinet.py` and
  `forms.py` (resources for django-cabinet and feincms3-forms), `kinds.py` and
  `paths.py` (from autotranslate).
- `tests/testapp/snapshot.txt`: the `f3api_schema` output for the test app;
  regenerate it with `.venv/bin/python tests/manage.py f3api_schema >
  tests/testapp/snapshot.txt` and look at the diff.
- `feincms3_api/incubator/`: code which may become part of the package or go
  away; tested, not documented as part of the package, no stability promise.
  `incubator/autotranslate/` is feincms3-autotranslate, its app label stays
  `feincms3_autotranslate`. Its docs are in `docs/autotranslate.md`.

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
