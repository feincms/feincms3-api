import tomllib

from django.conf import settings


DEFAULTS = {
    # Dotted path of the translator class; the files backend needs none.
    "BACKEND": None,
    # Required by the anthropic backend, deliberately without a default.
    "MODEL": None,
    # A TOML file, see ``load_glossary()``.
    "GLOSSARY": None,
    "INSTRUCTIONS": "",
    # Fields holding a URL beyond URLField, ``url`` and ``*_url``.
    "LINK_FIELDS": [],
    # Fields which are never sent, ``app_label.model_name.field[.json.path]``.
    "EXCLUDE_FIELDS": [],
    # Hosts whose absolute URLs are first-party links; defaults to
    # ``ALLOWED_HOSTS``.
    "FIRST_PARTY_HOSTS": None,
    # A callable (or its dotted path) returning the public URL of a page.
    "PAGE_URL": None,
    # One request carries at most this many characters of source text, an
    # object with more is split into several requests.
    "MAX_REQUEST_CHARS": 20000,
}


def config():
    return DEFAULTS | getattr(settings, "FEINCMS3_AUTOTRANSLATE", {})


def first_language():
    return settings.LANGUAGES[0][0]


def load_glossary(path, *, source, target):
    """
    Read the glossary for one language pair

    The file looks like this::

        # Terms which are never translated, in any language.
        keep = ["Acme", "AcmeConnect"]

        [de.fr]
        BVG = "LPP"
        Vorsorgeausweis = "certificat de prévoyance"

    The result is ``{"keep": [...], "terms": {source term: target term}}``.
    """
    if not path:
        return {"keep": [], "terms": {}}
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return {
        "keep": list(data.get("keep", [])),
        "terms": dict(data.get(source, {}).get(target, {})),
    }
