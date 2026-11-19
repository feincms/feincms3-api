"""
The feincms3 content API from the command line (``f3api``)

Reads print JSON; ``site list``, ``resources``, ``list`` and ``tokens`` print tables
in a terminal (JSON with ``--json``, or when the output isn't a terminal).
Writes are dry runs unless ``--apply`` is given, and send the ETag of the
object as it was last read with this tool (``get``, ``list``, ``find``), so
they fail instead of overwriting someone else's change. Errors are printed
with their paths.
"""

import getpass
import json
import os
import sys

import click
from rich.text import Text

from feincms3_api_cli.client import APIError, Client, TransportError
from feincms3_api_cli.config import ETags, Sites, prog
from feincms3_api_cli.output import (
    cell,
    console,
    errors,
    json_option,
    message,
    output,
    table,
    wants_json,
)
from feincms3_api_cli.skills import skills


# Exit codes
OK, FAILED, USAGE, INVALID, CONFLICT, NOT_FOUND, FORBIDDEN = range(7)
STATUS_EXIT = {
    422: INVALID,
    409: CONFLICT,
    412: CONFLICT,
    428: CONFLICT,
    404: NOT_FOUND,
    401: FORBIDDEN,
    403: FORBIDDEN,
}


class CommandError(Exception):
    def __init__(self, message, code=FAILED):
        super().__init__(message)
        self.code = code


def main(argv=None):
    try:
        return cli.main(argv, prog_name=prog(), standalone_mode=False) or OK
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except click.Abort:
        errors.print("Aborted.")
        return FAILED
    except APIError as exc:
        print_error(exc)
        return STATUS_EXIT.get(exc.status, FAILED)
    except (CommandError, TransportError) as exc:
        message(str(exc))
        return getattr(exc, "code", FAILED)


def print_error(exc):
    data = exc.data if isinstance(exc.data, dict) else {"detail": exc.data}
    prefix = f"operation {data['operation']}: " if "operation" in data else ""
    errors.print(
        Text.assemble(
            f"{prog()}: ", (f"{exc.status}", "bold red"), f" {prefix}"
        ).append(str(data.get("detail", "")))
    )
    for error in data.get("errors") or ():
        if isinstance(error, dict):
            path = error.get("path") or error.get("loc") or ""
            text = error.get("message") or error.get("msg") or ""
            errors.print(Text.assemble("  ", (str(path), "bold"), f": {text}"))
    for key, value in data.items():
        if key not in {"detail", "errors", "operation"}:
            errors.print(Text.assemble("  ", (key, "bold"), f": {json.dumps(value)}"))


def read_json(path):
    try:
        if path in {None, "-"}:
            return json.load(sys.stdin)
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        raise CommandError(
            f"Can't read JSON from {path or 'stdin'}: {exc}", USAGE
        ) from exc


def connect():
    site = click.get_current_context().find_root().params.get("site")
    try:
        url, token = Sites().resolve(site)
    except LookupError as exc:
        raise CommandError(str(exc), USAGE) from exc
    return Client(url, token), ETags(url)


def _etag(wire):
    return (wire or {}).get("meta", {}).get("etag")


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    epilog="Exit codes: 0 done, 1 failed, 2 usage, 3 invalid data (with"
    " paths), 4 conflict (ETag, references), 5 not found, 6 not allowed.",
)
@click.option("--site", help="A site added with `site add`.")
def cli(site):
    """The feincms3 content API from the command line."""


# Sites ######################################################################


@cli.group()
def site():
    """The sites and their tokens."""


@site.command("add")
@click.argument("name")
@click.argument("url")
@click.option("--token-stdin", is_flag=True, help="Read the token from stdin.")
def site_add(name, url, token_stdin):
    """Add a site; asks for the token. URL is where the API is, e.g.
    https://example.com/api/"""
    token = sys.stdin.readline().strip() if token_stdin else getpass.getpass("Token: ")
    Sites().add(name, url, token)


@site.command("list")
@json_option
def site_list(as_json):
    """The sites."""
    data = Sites().data
    sites = {
        name: {"url": site["url"], "default": name == data.get("default")}
        for name, site in data["sites"].items()
    }
    if wants_json(as_json):
        output(sites)
        return
    rows = table("Site", "URL", "Default")
    for name, site in sites.items():
        rows.add_row(name, site["url"], "yes" if site["default"] else "")
    console.print(rows)


@site.command("remove")
@click.argument("name")
def site_remove(name):
    """Remove a site."""
    try:
        Sites().remove(name)
    except KeyError as exc:
        raise CommandError(f"No site {name!r}.", USAGE) from exc


@site.command("default")
@click.argument("name")
def site_default(name):
    """Use a site by default."""
    try:
        Sites().set_default(name)
    except KeyError as exc:
        raise CommandError(f"No site {name!r}.", USAGE) from exc


# Tokens #####################################################################


def _site_name():
    site = click.get_current_context().find_root().params.get("site")
    try:
        return Sites().resolve_name(site)
    except LookupError as exc:
        raise CommandError(str(exc), USAGE) from exc


def _forget(name):
    """Forget the site whose token was revoked, and say so"""
    if name is None:
        message("unset F3API_TOKEN, the token doesn't work anymore.")
    else:
        Sites().remove(name)
        message(
            f"removed the site {name!r}; add it again with a new token"
            f" ({prog()} site add)."
        )


@cli.command()
def logout():
    """Revoke the token in use and forget the site."""
    name = _site_name()
    client, _etags = connect()
    try:
        client.request("DELETE", "tokens/current/")
    except APIError as exc:
        # 401: the token doesn't work anymore (expired, revoked), it's gone
        if exc.status != 401:
            _forget(name)
            data = exc.data if isinstance(exc.data, dict) else {}
            raise CommandError(
                f"couldn't revoke the token ({exc.status} {data.get('detail', '')});"
                " delete it in the admin."
            ) from exc
    except TransportError as exc:
        _forget(name)
        raise CommandError(
            f"couldn't revoke the token ({exc}); delete it in the admin."
        ) from exc
    _forget(name)


@cli.group(invoke_without_command=True)
@click.pass_context
def tokens(ctx):
    """Your tokens; without a command, lists them."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(tokens_list)


def _when(value):
    return value[:16].replace("T", " ") if value else ""


@tokens.command("list")
@json_option
def tokens_list(as_json):
    """Your tokens which haven't expired; * marks the one in use."""
    client, _etags = connect()
    _status, data = client.request("GET", "tokens/")
    if wants_json(as_json):
        output(data)
        return
    rows = table("ID>", "Name", "Prefix", "Access", "Created", "Last used", "Expires")
    for token in data:
        rows.add_row(
            f"{'*' if token['current'] else ''}{token['id']}",
            token["name"],
            token["prefix"],
            token["access"],
            _when(token["created_at"]),
            _when(token["last_used_at"]),
            _when(token["expires_at"]),
        )
    console.print(rows)


@tokens.command("delete")
@click.argument("ids", nargs=-1, type=int, required=True)
@json_option
def tokens_delete(ids, as_json):
    """Revoke tokens by ID (see `tokens list`), e.g. leaked ones.

    Revoking the token in use forgets the site, like `logout`. Read-only
    tokens may only revoke themselves."""
    name = _site_name()
    client, _etags = connect()
    _status, data = client.request("GET", "tokens/")
    current = {token["id"] for token in data if token["current"]}
    deleted = []
    # The token in use last, the others need it
    for pk in sorted(set(ids), key=lambda pk: pk in current):
        _status, token = client.request("DELETE", f"tokens/{pk}/")
        deleted.append(token)
        message(f"revoked {token['name'] or token['prefix']} ({pk}).")
        if token["current"]:
            _forget(name)
    if wants_json(as_json):
        output(deleted)


# Reading ####################################################################


@cli.command()
@json_option
def resources(as_json):
    """The resources."""
    client, _etags = connect()
    _status, data = client.request("GET", "schema/")
    items = [
        {"name": r["name"], "type": r["type"], "title": r["title"]}
        for r in data["resources"]
    ]
    if wants_json(as_json):
        output(items)
        return
    rows = table("Resource", "Type", "Title")
    for item in items:
        rows.add_row(item["name"], item["type"], item["title"])
    console.print(rows)


@cli.command()
@click.argument("type", required=False)
def schema(type):
    """The regions and plugins, or one TYPE's fields (e.g. pages.page)."""
    client, _etags = connect()
    path = f"schema/{type}/" if type else "schema/"
    output(client.request("GET", path)[1])


@cli.command("list")
@click.argument("resource")
@click.option("-f", "--filter", "filters", multiple=True, metavar="KEY=VALUE")
@click.option("-q", "q", help="Search.")
@click.option("--limit", type=int)
@click.option("--offset", type=int)
@click.option("--fields", help="Only these fields, comma separated.")
@click.option("--content", is_flag=True, help="With content.")
@click.option("--expand", is_flag=True, help="Describe references.")
@json_option
def list_objects(resource, filters, q, limit, offset, fields, content, expand, as_json):
    """List objects."""
    client, etags = connect()
    params = dict(_pair(f) for f in filters)
    params |= {
        "limit": limit,
        "offset": offset,
        "q": q,
        "fields": fields,
        "content": "true" if content else None,
        "expand": "references" if expand else None,
    }
    _status, data = client.request("GET", f"{resource}/", params=params)
    # Without content, an item's ETag isn't the object's.
    if content:
        etags.update({(resource, item["id"]): _etag(item) for item in data["items"]})
    if wants_json(as_json):
        output(data)
        return
    items = data["items"]
    names = [name for name in (fields or "").split(",") if name]
    with_path = not names and any("path" in item.get("meta", {}) for item in items)
    rows = table("ID>", *(names or ["Title", *["Path"] * with_path]))
    for item in items:
        meta = item.get("meta", {})
        if names:
            values = [cell(item.get("fields", {}).get(name)) for name in names]
        else:
            values = [cell(meta.get("title")), *[cell(meta.get("path"))] * with_path]
        rows.add_row(str(item["id"]), *values)
    console.print(rows)
    start = (offset or 0) + 1
    console.print(
        f"{start}–{start + len(items) - 1} of {data['count']}"
        if items
        else f"None of {data['count']}"
    )


def _pair(value):
    key, sep, rest = value.partition("=")
    if not sep:
        raise CommandError(f"Expected key=value, not {value!r}.", USAGE)
    return key, rest


@cli.command()
@click.argument("resource")
@click.argument("id")
@click.option("--nested", is_flag=True, help="Containers with children.")
@click.option("--expand", is_flag=True, help="Describe references.")
def get(resource, id, nested, expand):
    """One object."""
    client, etags = connect()
    _status, data = client.request(
        "GET",
        f"{resource}/{id}/",
        params={
            "nested": "true" if nested else None,
            "expand": "references" if expand else None,
        },
    )
    etags.update({(resource, data["id"]): _etag(data)})
    output(data)


@cli.command()
@click.argument("path")
@click.option("--resource", default="pages", show_default=True)
@click.option("--host")
def find(path, resource, host):
    """An object by its path."""
    client, etags = connect()
    _status, data = client.request(
        "GET", f"{resource}/find/", params={"path": path, "host": host}
    )
    etags.update({(resource, data["id"]): _etag(data)})
    output(data)


@cli.command()
@click.argument("resource")
@click.argument("id")
@click.argument("type")
@click.argument("plugin_id")
def plugin(resource, id, type, plugin_id):
    """One plugin of an object."""
    client, _etags = connect()
    output(client.request("GET", f"{resource}/{id}/plugins/{type}/{plugin_id}/")[1])


# Writing ####################################################################


def writing(*, etag=True):
    def decorator(function):
        if etag:
            function = click.option(
                "--force", is_flag=True, help="Overwrite whatever is there."
            )(function)
            function = click.option("--etag", help="Instead of the one last read.")(
                function
            )
        return click.option(
            "--apply", is_flag=True, help="Save; without it, a dry run."
        )(function)

    return decorator


file_argument = click.argument("file", required=False)
FILE_HELP = "FILE is JSON; stdin if left out or -."


def _written(apply, data):
    if not apply:
        message("dry run, nothing saved; run again with --apply to save.")
    output(data)


def _if_match(etags, resource, pk, *, etag, force):
    if force:
        return "*"
    if etag:
        return etag
    if etag := etags.get(resource, pk):
        return etag
    raise CommandError(
        f"No ETag for {resource}/{pk}: read it first ({prog()} get {resource} {pk}),"
        " or pass --etag or --force.",
        CONFLICT,
    )


def _params(apply):
    return {"dry_run": None if apply else "true"}


@cli.command(epilog=FILE_HELP)
@click.argument("resource")
@file_argument
@writing(etag=False)
def create(resource, file, apply):
    """Create an object from JSON."""
    client, etags = connect()
    _status, data = client.request(
        "POST", f"{resource}/", params=_params(apply), body=read_json(file)
    )
    if apply:
        etags.update({(resource, data["id"]): _etag(data)})
    _written(apply, data)


@cli.command(epilog=FILE_HELP)
@click.argument("resource")
@click.argument("id")
@file_argument
@writing()
def update(resource, id, file, apply, etag, force):
    """Change fields, replace regions."""
    client, etags = connect()
    _status, data = client.request(
        "PATCH",
        f"{resource}/{id}/",
        params=_params(apply),
        body=read_json(file),
        headers={"If-Match": _if_match(etags, resource, id, etag=etag, force=force)},
    )
    if apply:
        etags.update({(resource, id): _etag(data)})
    _written(apply, data)


@cli.command(epilog=FILE_HELP)
@click.argument("resource")
@click.argument("id")
@file_argument
@writing()
def content(resource, id, file, apply, etag, force):
    """Replace the regions given."""
    client, etags = connect()
    body = read_json(file)
    # The body, or only its regions (which may include one called "content")
    if not (isinstance(body, dict) and isinstance(body.get("content"), dict)):
        body = {"content": body}
    _status, data = client.request(
        "PUT",
        f"{resource}/{id}/content/",
        params=_params(apply),
        body=body,
        headers={"If-Match": _if_match(etags, resource, id, etag=etag, force=force)},
    )
    if apply:
        etags.update({(resource, id): _etag(data)})
    _written(apply, data)


@cli.command("update-plugin", epilog=FILE_HELP)
@click.argument("resource")
@click.argument("id")
@click.argument("type")
@click.argument("plugin_id")
@file_argument
@writing()
def update_plugin(resource, id, type, plugin_id, file, apply, etag, force):
    """Change one plugin's fields."""
    client, etags = connect()
    body = read_json(file)
    if not (isinstance(body, dict) and set(body) == {"fields"}):
        body = {"fields": body}
    _status, data = client.request(
        "PATCH",
        f"{resource}/{id}/plugins/{type}/{plugin_id}/",
        params=_params(apply),
        body=body,
        headers={"If-Match": _if_match(etags, resource, id, etag=etag, force=force)},
    )
    if apply:
        etags.update({(resource, id): _etag(data)})
    _written(apply, data)


@cli.command()
@click.argument("resource")
@click.argument("id")
@writing()
def delete(resource, id, apply, etag, force):
    """Delete an object."""
    client, etags = connect()
    client.request(
        "DELETE",
        f"{resource}/{id}/",
        params=_params(apply),
        headers={"If-Match": _if_match(etags, resource, id, etag=etag, force=force)},
    )
    if apply:
        etags.update({(resource, id): None})
        message(f"deleted {resource}/{id}.")
    else:
        message(
            f"dry run, {resource}/{id} can be deleted; run again with --apply"
            " to delete it."
        )


@cli.command("cache-clear")
@click.argument("resource")
@click.argument("id")
def cache_clear(resource, id):
    """Delete an object's cached rendering."""
    client, _etags = connect()
    client.request("DELETE", f"{resource}/{id}/cache/")


@cli.command()
@click.argument("file")
def upload(file):
    """Stage a file; prints the reference to write."""
    client, _etags = connect()
    try:
        with open(file, "rb") as f:
            data = f.read()
    except OSError as exc:
        raise CommandError(f"Can't read {file}: {exc}", USAGE) from exc
    output(client.upload(os.path.basename(file), data)[1])


@cli.command(epilog=FILE_HELP)
@file_argument
@writing(etag=False)
def batch(file, apply):
    """Several writes in one transaction."""
    client, etags = connect()
    body = read_json(file)
    if isinstance(body, list):
        body = {"operations": body}
    operations = body.get("operations") if isinstance(body, dict) else None
    for operation in operations if isinstance(operations, list) else ():
        if (
            isinstance(operation, dict)
            and operation.get("op") != "create"
            and "etag" not in operation
            and not isinstance(operation.get("id"), dict)
            and (etag := etags.get(operation.get("resource"), operation.get("id")))
        ):
            operation["etag"] = etag
    _status, data = client.request("POST", "batch/", params=_params(apply), body=body)
    if apply:
        entries = {}
        for operation, result in zip(operations, data["results"], strict=False):
            resource = operation["resource"]
            if operation["op"] == "create":
                entries[resource, result["id"]] = _etag(result)
                continue
            pk = operation["id"]
            if isinstance(pk, dict):
                pk = data["refs"][pk["ref"]]
            entries[resource, pk] = (
                None if operation["op"] == "delete" else _etag(result)
            )
        etags.update(entries)
    _written(apply, data)


cli.add_command(skills)


def main_exit():
    sys.exit(main())
