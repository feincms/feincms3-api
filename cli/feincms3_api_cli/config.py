"""
Sites (URL and token) and the ETags of what was read

``F3API_URL`` and ``F3API_TOKEN`` replace the default site;
``F3API_CONFIG`` and ``F3API_CACHE`` move the files (for tests).
"""

import json
import os
import sys
from pathlib import Path


def prog():
    """The command's name as run: ``f3api``, or ``feincms3-api-cli`` (uvx)"""
    name = Path(sys.argv[0]).name
    return name if name in {"f3api", "feincms3-api-cli"} else "f3api"


def _dir(env, xdg, default):
    if path := os.environ.get(env):
        return Path(path)
    return Path(os.environ.get(xdg) or Path.home() / default) / "f3api"


def config_dir():
    return _dir("F3API_CONFIG", "XDG_CONFIG_HOME", ".config")


def cache_dir():
    return _dir("F3API_CACHE", "XDG_CACHE_HOME", ".cache")


def _load(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def _save(path, data, *, private=False):
    path.parent.mkdir(mode=0o700 if private else 0o777, parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    # Readable by the user only from the start, not after writing
    fd = os.open(
        tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o666
    )
    if private and hasattr(os, "fchmod"):  # Not on Windows before 3.13
        os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(path)


class Sites:
    def __init__(self):
        self.path = config_dir() / "sites.json"
        self.data = _load(self.path) or {"default": None, "sites": {}}

    def save(self):
        _save(self.path, self.data, private=True)

    def add(self, name, url, token):
        self.data["sites"][name] = {"url": url, "token": token}
        if not self.data.get("default"):
            self.data["default"] = name
        self.save()

    def remove(self, name):
        self.data["sites"].pop(name)
        if self.data.get("default") == name:
            self.data["default"] = next(iter(self.data["sites"]), None)
        self.save()

    def set_default(self, name):
        if name not in self.data["sites"]:
            raise KeyError(name)
        self.data["default"] = name
        self.save()

    def resolve_name(self, name=None):
        """
        The site's name: ``name`` (``--site``), else ``None`` for
        ``F3API_URL`` and ``F3API_TOKEN``, else the default site's
        """
        if not name and os.environ.get("F3API_URL"):
            return None
        name = name or os.environ.get("F3API_SITE") or self.data.get("default")
        if not name or name not in self.data["sites"]:
            raise LookupError(
                f"No site: add one with `{prog()} sites add NAME URL`, or set"
                " F3API_URL and F3API_TOKEN."
            )
        return name

    def resolve(self, name=None):
        """``(url, token)`` of the site (see ``resolve_name()``)"""
        if (name := self.resolve_name(name)) is None:
            return os.environ["F3API_URL"], os.environ.get("F3API_TOKEN")
        site = self.data["sites"][name]
        return site["url"], site["token"]


class ETags:
    """The ETag of each object as last read, per site"""

    def __init__(self, url):
        self.path = cache_dir() / "etags.json"
        self.url = url.rstrip("/")

    def _key(self, resource, pk):
        return f"{self.url} {resource}/{pk}"

    def get(self, resource, pk):
        return _load(self.path).get(self._key(resource, pk))

    def update(self, entries):
        """``entries``: ``{(resource, id): etag or None}``"""
        if not entries:
            return
        data = _load(self.path)
        for (resource, pk), etag in entries.items():
            if etag is None:
                data.pop(self._key(resource, pk), None)
            else:
                data[self._key(resource, pk)] = etag
        _save(self.path, data)
