"""
The admin's inline formsets, bound to the API's data

A write binds every inline of the object, plugins and other inlines, to the
formset the admin would build (``inline.get_formset()``, the same prefix and
queryset), with a form for every row the admin would post: the unchanged
ones too, so a formset's own ``clean()`` sees all of them. Saving then goes
through ``save_related()`` and ``save_formset()``, as in the admin.
"""

import dataclasses

from django.forms.formsets import DELETION_FIELD_NAME

from feincms3_api.adapters import form_errors, prepare_form
from feincms3_api.resources import fk_name


@dataclasses.dataclass
class Row:
    """One form of a formset: an existing object or a new one"""

    #: The object; a new one isn't saved yet (no ``pk``)
    instance: object
    #: Where it is on the wire, for errors (``content.main[2]``, or ``""``)
    path: str
    #: The ``fields`` sent, ``None`` if it stays as it is
    wire: dict | None = None
    #: Form data the API sets itself (a plugin's region, ordering, depth)
    extra: dict = dataclasses.field(default_factory=dict)
    delete: bool = False

    @property
    def new(self):
        return self.instance.pk is None

    def fields_path(self):
        return f"{self.path}.fields" if self.path else "fields"


@dataclasses.dataclass
class BoundFormset:
    inline: object
    formset: object
    rows: list
    #: The path of errors which aren't one form's (the formset's ``clean()``)
    path: str


def bind_formset(request, inline, obj, rows, context, adapter, *, prefix, path):
    """``(BoundFormset, errors)``: ``rows`` bound to the inline's formset"""
    change = obj.pk is not None
    formset_class = inline.get_formset(request, obj if change else None)
    pk_name = inline.model._meta.pk.name
    # The admin's formsets have the existing objects first
    rows = [row for row in rows if not row.new] + [row for row in rows if row.new]
    initial = sum(1 for row in rows if not row.new)
    data = {
        f"{prefix}-TOTAL_FORMS": len(rows),
        f"{prefix}-INITIAL_FORMS": initial,
        f"{prefix}-MIN_NUM_FORMS": 0,
        f"{prefix}-MAX_NUM_FORMS": len(rows),
    }
    errors = []
    for index, row in enumerate(rows):
        form_data, row_errors = adapter.form_data(
            context,
            row.instance,
            row.wire or {},
            path=row.fields_path(),
            extra=row.extra,
        )
        errors.extend(row_errors)
        for name, value in (form_data or {}).items():
            data[f"{prefix}-{index}-{name}"] = value
        if not row.new:
            data[f"{prefix}-{index}-{pk_name}"] = row.instance.pk
        if row.delete:
            data[f"{prefix}-{index}-{DELETION_FIELD_NAME}"] = True

    keep_always = {fk_name(inline)}

    class APIFormSet(formset_class):
        def _construct_form(self, i, **kwargs):
            form = super()._construct_form(i, **kwargs)
            row = rows[i]
            prepare_form(form, context, keep=keep_always | set(row.extra))
            if row.new:
                # Sent to be created, even if all its values are defaults
                form.empty_permitted = False
                form.has_changed = lambda: True
            return form

    formset = APIFormSet(
        data=data,
        files={},
        instance=obj,
        prefix=prefix,
        queryset=inline.get_queryset(request),
    )
    if not inline.has_change_permission(request, obj if change else None):
        # As the admin does: view-only rows aren't validated, unless deleted
        for form, row in zip(formset.initial_forms, rows, strict=False):
            if not row.delete:
                form._errors = {}
                form.cleaned_data = form.initial
    return BoundFormset(inline, formset, rows, path), errors


def formset_errors(bound):
    """A bound formset's errors, with the paths of its rows"""
    formset = bound.formset
    errors = []
    for form, row in zip(formset.forms, bound.rows, strict=True):
        if row.delete:
            continue
        errors.extend(form_errors(form, row.fields_path()))
    errors.extend(
        {"path": bound.path, "message": str(message)}
        for message in formset.non_form_errors()
    )
    return errors


def prefixes(formset_classes):
    """The admin's formset prefixes, numbered where two are the same"""
    seen = {}
    result = []
    for formset_class in formset_classes:
        prefix = formset_class.get_default_prefix()
        seen[prefix] = seen.get(prefix, 0) + 1
        if seen[prefix] != 1 or not prefix:
            prefix = f"{prefix}-{seen[prefix]}"
        result.append(prefix)
    return result
