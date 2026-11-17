"""
Files stored by writes which are rolled back

A file field stores its file when the object is saved, and rolling back the
transaction (a dry run, a failed write or batch) leaves the file in the
storage. The outermost write or batch collects the files its uploads were
stored as, and deletes them unless it's saved.
"""

import contextvars
from contextlib import contextmanager

from django.db.models.fields.files import FieldFile


_current = contextvars.ContextVar("feincms3_api_stored", default=None)


class _Collector:
    def __init__(self):
        self.uploads = []  # The files handed to forms
        self.stored = []  # (storage, name) of what they were stored as


@contextmanager
def collecting(*, dry_run):
    """
    Around a write's or batch's transaction: deletes the stored uploads if
    it's a dry run or fails. Writes in a batch leave it to the batch.
    """
    if _current.get() is not None:
        yield
        return
    collector = _Collector()
    token = _current.set(collector)
    saved = False
    try:
        yield
        saved = not dry_run
    finally:
        _current.reset(token)
        if not saved:
            for storage, name in collector.stored:
                storage.delete(name)


def handed_to_form(file):
    """An upload's file, given to a form"""
    if (collector := _current.get()) is not None:
        collector.uploads.append(file)
    return file


def record(forms):
    """The files the forms' uploads were stored as, once they're saved"""
    if (collector := _current.get()) is None or not collector.uploads:
        return
    for form in forms:
        for name, value in (getattr(form, "cleaned_data", None) or {}).items():
            if not any(value is upload for upload in collector.uploads):
                continue
            stored = getattr(form.instance, name, None)
            # Committed: stored by this save, not the upload's name only
            if isinstance(stored, FieldFile) and stored.name and stored._committed:
                collector.stored.append((stored.storage, stored.name))
