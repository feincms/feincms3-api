"""
The review file: every translated object, original and translation side by
side, for people
"""

from django.apps import apps
from django.urls import NoReverseMatch, reverse
from django.utils.html import escape

from feincms3_autotranslate.strings import Unit, object_strings, unit_title


STYLE = """
body { font: 15px/1.45 system-ui, sans-serif; margin: 2rem auto; max-width: 80rem; padding: 0 1rem; color: #222; }
h1 { font-size: 1.5rem; }
h2 { font-size: 1.15rem; margin: 2.5rem 0 .25rem; }
.meta { color: #666; margin: 0 0 .75rem; }
table { border-collapse: collapse; width: 100%; table-layout: fixed; }
th, td { border-top: 1px solid #ddd; padding: .5rem; vertical-align: top; text-align: left; }
th.key { width: 14rem; font-weight: normal; color: #666; font-size: 12px; word-break: break-all; }
.failed { background: #fde8e8; border: 1px solid #e99; padding: .5rem 1rem; }
td > :first-child { margin-top: 0; }
td > :last-child { margin-bottom: 0; }
"""


def _admin_url(label, pk):
    model = apps.get_model(label)
    try:
        return reverse(
            f"admin:{model._meta.app_label}_{model._meta.model_name}_change",
            args=[pk],
        )
    except NoReverseMatch:
        return ""


def _cell(kind, text):
    return text if kind == "html" else escape(text).replace("\n", "<br>")


def review_html(job, translated):
    """``job`` holds the originals, ``translated`` is the translated file"""
    by_key = {
        f"{raw['model']}.{raw['pk']}": raw
        for dump in translated["dumps"]
        for raw in dump["objects"]
    }
    statuses = translated["units"]
    parts = [
        "<!doctype html><meta charset='utf-8'>",
        f"<title>Review {job.source} → {job.target}</title>",
        f"<style>{STYLE}</style>",
        f"<h1>Translations {escape(job.source)} → {escape(job.target)}</h1>",
    ]
    if translated.get("model"):
        parts.append(f"<p class='meta'>Model: {escape(translated['model'])}</p>")

    failed = [
        (key, state) for key, state in statuses.items() if state["status"] == "failed"
    ]
    if failed:
        parts.append("<div class='failed'><h2>Failed</h2><ul>")
        parts.extend(
            f"<li>{escape(key)}: {escape(state.get('reason', ''))}</li>"
            for key, state in failed
        )
        parts.append("</ul></div>")

    for key, unit in job.units.items():
        if statuses.get(key, {}).get("status") != "translated":
            continue
        model = apps.get_model(unit.label)
        fields = unit.owner["fields"]
        where = fields.get("path") or fields.get("slug") or ""
        admin = _admin_url(unit.label, unit.pk)
        parts.append(
            f"<h2>{escape(unit_title(unit, job.registry))}</h2>"
            f"<p class='meta'>{escape(model._meta.verbose_name)} {escape(key)}"
            f" {escape(where)}"
            + (f" · <a href='{escape(admin)}'>admin</a>" if admin else "")
            + "</p><table>"
        )
        translated_unit = Unit(
            key=key,
            kind=unit.kind,
            owner=by_key[key],
            children=[by_key[f"{raw['model']}.{raw['pk']}"] for raw in unit.children],
        )
        for original, copy in zip(unit.objects(), translated_unit.objects()):
            source = object_strings(original)
            target = object_strings(copy)
            for string_key, (kind, text) in source.items():
                _kind, new = target.get(string_key, (kind, ""))
                parts.append(
                    f"<tr><th class='key'>{escape(string_key)}</th>"
                    f"<td>{_cell(kind, text)}</td><td>{_cell(kind, new)}</td></tr>"
                )
        parts.append("</table>")
    return "\n".join(parts) + "\n"
