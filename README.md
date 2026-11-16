# feincms3-api

A [django-ninja](https://django-ninja.dev/) API for reading and writing the
content of [feincms3](https://feincms3.readthedocs.io/) sites: pages and
other objects with
[django-content-editor](https://django-content-editor.readthedocs.io/)
regions and plugins,
[django-json-schema-editor](https://github.com/matthiask/django-json-schema-editor)
data and [django-cabinet](https://github.com/matthiask/django-cabinet) files.
For scripts, importers and agents, next to the Django admin, not instead of
it.

**Status:** early development. Reading works, writing doesn't yet. The plan
is in [specs/api.md](specs/api.md).

## Incubator

`feincms3_api.incubator` holds code which may become part of the package or
may go away: tested, but not documented as part of the package, and without
any promise of stability. At the moment that's
`feincms3_api.incubator.autotranslate`, which machine-translates feincms3
content and loads it as inactive copies, see
[docs/autotranslate.md](docs/autotranslate.md).

## Credits

feincms3-api grows out of feincms3-autotranslate, started by Moritz Wettstein
and Matthias Kestenholz at [Feinheit](https://www.feinheit.ch/) in October
2026. Its content introspection (which fields hold text, HTML or links, JSON
schemas, plugin discovery) is the base of this package.
