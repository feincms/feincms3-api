"""
The content API: registered resources, the operations on them, and the
django-ninja router exposing them

The operations are plain methods taking a request (for the admin's methods,
which need one) and returning wire data. The endpoints only wrap them, so
management commands and other code can use them without HTTP, also several
of them in one transaction.
"""

from django.utils.functional import cached_property
from ninja import NinjaAPI

from feincms3_api.adapters import AdapterRegistry
from feincms3_api.auth import default_auth
from feincms3_api.codecs import codecs as default_codecs
from feincms3_api.errors import NotFoundError
from feincms3_api.reading import ReadingMixin
from feincms3_api.schema import SchemaMixin
from feincms3_api.writing import WritingMixin


_instances = []


class ContentAPI(ReadingMixin, SchemaMixin, WritingMixin):
    def __init__(self, *, auth=None, codecs=None, adapters=None):
        self.auth = auth if auth is not None else default_auth()
        self.codecs = codecs or default_codecs.copy()
        self.adapters = adapters or AdapterRegistry()
        self._resources = {}
        _instances.append(self)

    # Registration ###########################################################

    def register(self, resource):
        if "router" in self.__dict__:
            raise RuntimeError("Register resources before using the router.")
        if resource.name in self._resources or resource.name in {"schema", "uploads"}:
            raise ValueError(f"The resource name {resource.name!r} is taken.")
        self._resources[resource.name] = resource
        return resource

    @property
    def resources(self):
        return list(self._resources.values())

    def resource(self, name):
        try:
            return self._resources[name]
        except KeyError as exc:
            raise NotFoundError(f"No resource {name!r}.") from exc

    @cached_property
    def components(self):
        """The OpenAPI schema's components, built when first requested"""
        from feincms3_api.openapi import Components  # noqa: PLC0415

        return Components(self)

    @cached_property
    def router(self):
        from feincms3_api.http import build_router  # noqa: PLC0415

        return build_router(self)

    def as_api(self, **kwargs):
        """A ``NinjaAPI`` of its own, for projects without one"""
        kwargs.setdefault("title", "Content API")
        kwargs.setdefault("urls_namespace", "feincms3_api")
        api = NinjaAPI(**kwargs)
        api.add_router("/", self.router)
        return api
