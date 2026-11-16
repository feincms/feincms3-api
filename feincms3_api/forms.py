"""
feincms3-forms: configured forms and their fields

The fields are plugins (proxies of one model per field type, and plugins of
their own); a resource for the forms only adds the form type's validation,
which the admin shows as messages, to ``meta.warnings``.
"""

from django.contrib.messages import constants

from feincms3_api.resources import ERROR, WARNING, ObjectResource, warning


class ConfiguredFormResource(ObjectResource):
    """A ``ConfiguredForm`` subclass"""

    def check(self, request, obj):
        warnings = list(super().check(request, obj))
        if (type := obj.type) is None:
            warnings.append(
                warning(
                    f"The form type {obj.form_type!r} doesn't exist; the form"
                    " can't be validated.",
                    level=ERROR,
                    path="fields.form_type",
                )
            )
            return warnings
        warnings.extend(
            warning(
                message.message,
                level=ERROR if message.level >= constants.ERROR else WARNING,
            )
            for message in type.validate(obj)
        )
        return warnings
