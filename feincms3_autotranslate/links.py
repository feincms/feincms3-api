"""
First-party links in HTML and link fields

A first-party link is a path (``/de/angebot/``) or an absolute URL whose host
belongs to the site. The loader resolves it to an object and writes the
counterpart's URL instead; this module only knows about URLs.
"""

import html
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.db import models
from django.utils.module_loading import import_string

from feincms3_autotranslate.conf import config


HREF_RE = re.compile(r"""(<a\b[^>]*?\shref=)(["'])(.*?)\2""", re.IGNORECASE | re.DOTALL)


def first_party_hosts():
    hosts = config()["FIRST_PARTY_HOSTS"]
    if hosts is None:
        hosts = [host for host in settings.ALLOWED_HOSTS if host != "*"]
    return hosts


def _host_matches(host, patterns):
    host = host.lower()
    return any(
        host == pattern.lower()
        or (pattern.startswith(".") and host.endswith(pattern.lower()))
        or host == pattern.lower().lstrip(".")
        for pattern in patterns
    )


def split_url(url):
    """
    ``(origin, path, rest)`` of a first-party URL, ``None`` for anything else

    ``origin`` is ``"https://example.com"`` for absolute URLs and ``""`` for
    paths, ``rest`` is the query string and the fragment.
    """
    if not url or not isinstance(url, str):
        return None
    parts = urlsplit(url.strip())
    if parts.scheme and parts.scheme not in {"http", "https"}:
        return None
    if parts.netloc:
        if not _host_matches(parts.hostname or "", first_party_hosts()):
            return None
        origin = (
            f"{parts.scheme}://{parts.netloc}" if parts.scheme else f"//{parts.netloc}"
        )
    elif parts.path.startswith("/"):
        origin = ""
    else:
        # Fragments, mailto:, relative paths without a slash
        return None
    rest = (f"?{parts.query}" if parts.query else "") + (
        f"#{parts.fragment}" if parts.fragment else ""
    )
    return origin, parts.path, rest


def host_of(origin):
    return urlsplit(origin).hostname if origin else None


def map_html(text, map_url):
    """Run every ``<a href>`` of ``text`` through ``map_url(url)``"""

    def replace(match):
        url = html.unescape(match[3])
        new = map_url(url)
        if new == url:
            return match[0]
        escaped = new.replace("&", "&amp;").replace(match[2], html.escape(match[2]))
        return f"{match[1]}{match[2]}{escaped}{match[2]}"

    return HREF_RE.sub(replace, text)


def path_scope(model):
    """
    The fields ``path`` is unique together with: ``()`` for feincms3,
    ``("language_code",)`` for feincms3-language-sites, ``("site",)`` for
    feincms3-sites
    """
    meta = model._meta
    if meta.get_field("path").unique:
        return ()
    for fields in meta.unique_together:
        if "path" in fields:
            return tuple(f for f in fields if f != "path")
    for constraint in meta.constraints:
        if isinstance(constraint, models.UniqueConstraint) and "path" in (
            constraint.fields or ()
        ):
            return tuple(f for f in constraint.fields if f != "path")
    return ()


def page_location(page):
    """
    ``(host, path)`` of a page: the host is ``None`` when the site has only
    one, as with feincms3 itself
    """
    if custom := config()["PAGE_URL"]:
        custom = import_string(custom) if isinstance(custom, str) else custom
        return custom(page)
    site = getattr(page, "site", None)
    if isinstance(site, dict) and site.get("host"):
        # feincms3-language-sites
        return site["host"], page.path
    if site is not None and hasattr(site, "get_host"):
        # feincms3-sites
        return site.get_host(), page.path
    return None, page.get_absolute_url()
