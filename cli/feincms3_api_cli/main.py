"""
The feincms3 content API from the command line (``f3api``)

Reads print JSON. Writes are dry runs unless ``--apply`` is given, and send
the ETag of the object as it was last read with this tool (``get``,
``list``, ``find``), so they fail instead of overwriting someone else's
change. Errors are printed with their paths.
"""

import argparse
import getpass
import json
import os
import sys

from feincms3_api_cli.client import APIError, Client, TransportError
from feincms3_api_cli.config import ETags, Sites, prog


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
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "handler", None):
        parser.print_help()
        return USAGE
    try:
        return args.handler(args) or OK
    except APIError as exc:
        print_error(exc)
        return STATUS_EXIT.get(exc.status, FAILED)
    except CommandError as exc:
        print(f"{prog()}: {exc}", file=sys.stderr)
        return exc.code
    except TransportError as exc:
        print(f"{prog()}: {exc}", file=sys.stderr)
        return FAILED


def print_error(exc):
    data = exc.data if isinstance(exc.data, dict) else {"detail": exc.data}
    prefix = f"operation {data['operation']}: " if "operation" in data else ""
    print(f"{prog()}: {exc.status} {prefix}{data.get('detail', '')}", file=sys.stderr)
    for error in data.get("errors") or ():
        if isinstance(error, dict):
            path = error.get("path") or error.get("loc") or ""
            message = error.get("message") or error.get("msg") or ""
            print(f"  {path}: {message}", file=sys.stderr)
    for key, value in data.items():
        if key not in {"detail", "errors", "operation"}:
            print(f"  {key}: {json.dumps(value)}", file=sys.stderr)


def output(data):
    json.dump(data, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


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


def connect(args):
    try:
        url, token = Sites().resolve(args.site)
    except LookupError as exc:
        raise CommandError(str(exc), USAGE) from exc
    return Client(url, token), ETags(url)


def _etag(wire):
    return (wire or {}).get("meta", {}).get("etag")


# Sites ######################################################################


def site_add(args):
    token = (
        sys.stdin.readline().strip() if args.token_stdin else getpass.getpass("Token: ")
    )
    Sites().add(args.name, args.url, token)


def site_list(args):
    data = Sites().data
    output(
        {
            name: {"url": site["url"], "default": name == data.get("default")}
            for name, site in data["sites"].items()
        }
    )


def site_remove(args):
    try:
        Sites().remove(args.name)
    except KeyError as exc:
        raise CommandError(f"No site {args.name!r}.", USAGE) from exc


def site_default(args):
    try:
        Sites().set_default(args.name)
    except KeyError as exc:
        raise CommandError(f"No site {args.name!r}.", USAGE) from exc


# Reading ####################################################################


def resources(args):
    client, _etags = connect(args)
    _status, data = client.request("GET", "schema/")
    output(
        [
            {"name": r["name"], "type": r["type"], "title": r["title"]}
            for r in data["resources"]
        ]
    )


def schema(args):
    client, _etags = connect(args)
    path = f"schema/{args.type}/" if args.type else "schema/"
    output(client.request("GET", path)[1])


def list_objects(args):
    client, etags = connect(args)
    params = dict(_pair(f) for f in args.filter)
    params |= {
        "limit": args.limit,
        "offset": args.offset,
        "q": args.q,
        "fields": args.fields,
        "content": "true" if args.content else None,
        "expand": "references" if args.expand else None,
    }
    _status, data = client.request("GET", f"{args.resource}/", params=params)
    # Without content, an item's ETag isn't the object's.
    if args.content:
        etags.update(
            {(args.resource, item["id"]): _etag(item) for item in data["items"]}
        )
    output(data)


def _pair(value):
    key, sep, rest = value.partition("=")
    if not sep:
        raise CommandError(f"Expected key=value, not {value!r}.", USAGE)
    return key, rest


def get(args):
    client, etags = connect(args)
    _status, data = client.request(
        "GET",
        f"{args.resource}/{args.id}/",
        params={
            "nested": "true" if args.nested else None,
            "expand": "references" if args.expand else None,
        },
    )
    etags.update({(args.resource, data["id"]): _etag(data)})
    output(data)


def find(args):
    client, etags = connect(args)
    _status, data = client.request(
        "GET",
        f"{args.resource}/find/",
        params={"path": args.path, "host": args.host},
    )
    etags.update({(args.resource, data["id"]): _etag(data)})
    output(data)


def plugin(args):
    client, _etags = connect(args)
    path = f"{args.resource}/{args.id}/plugins/{args.type}/{args.plugin_id}/"
    output(client.request("GET", path)[1])


# Writing ####################################################################


def _written(args, data):
    if not args.apply:
        print(
            f"{prog()}: dry run, nothing saved; run again with --apply to save.",
            file=sys.stderr,
        )
    output(data)


def _if_match(args, etags, resource, pk):
    if args.force:
        return "*"
    if args.etag:
        return args.etag
    if etag := etags.get(resource, pk):
        return etag
    raise CommandError(
        f"No ETag for {resource}/{pk}: read it first ({prog()} get {resource} {pk}),"
        " or pass --etag or --force.",
        CONFLICT,
    )


def _params(args):
    return {"dry_run": None if args.apply else "true"}


def create(args):
    client, etags = connect(args)
    _status, data = client.request(
        "POST", f"{args.resource}/", params=_params(args), body=read_json(args.file)
    )
    if args.apply:
        etags.update({(args.resource, data["id"]): _etag(data)})
    _written(args, data)


def update(args):
    client, etags = connect(args)
    _status, data = client.request(
        "PATCH",
        f"{args.resource}/{args.id}/",
        params=_params(args),
        body=read_json(args.file),
        headers={"If-Match": _if_match(args, etags, args.resource, args.id)},
    )
    if args.apply:
        etags.update({(args.resource, args.id): _etag(data)})
    _written(args, data)


def content(args):
    client, etags = connect(args)
    body = read_json(args.file)
    # The body, or only its regions (which may include one called "content")
    if not (isinstance(body, dict) and isinstance(body.get("content"), dict)):
        body = {"content": body}
    _status, data = client.request(
        "PUT",
        f"{args.resource}/{args.id}/content/",
        params=_params(args),
        body=body,
        headers={"If-Match": _if_match(args, etags, args.resource, args.id)},
    )
    if args.apply:
        etags.update({(args.resource, args.id): _etag(data)})
    _written(args, data)


def update_plugin(args):
    client, etags = connect(args)
    body = read_json(args.file)
    if not (isinstance(body, dict) and set(body) == {"fields"}):
        body = {"fields": body}
    _status, data = client.request(
        "PATCH",
        f"{args.resource}/{args.id}/plugins/{args.type}/{args.plugin_id}/",
        params=_params(args),
        body=body,
        headers={"If-Match": _if_match(args, etags, args.resource, args.id)},
    )
    if args.apply:
        etags.update({(args.resource, args.id): _etag(data)})
    _written(args, data)


def delete(args):
    client, etags = connect(args)
    client.request(
        "DELETE",
        f"{args.resource}/{args.id}/",
        params=_params(args),
        headers={"If-Match": _if_match(args, etags, args.resource, args.id)},
    )
    if args.apply:
        etags.update({(args.resource, args.id): None})
        print(f"{prog()}: deleted {args.resource}/{args.id}.", file=sys.stderr)
    else:
        print(
            f"{prog()}: dry run, {args.resource}/{args.id} can be deleted; run"
            " again with --apply to delete it.",
            file=sys.stderr,
        )


def cache_clear(args):
    client, _etags = connect(args)
    client.request("DELETE", f"{args.resource}/{args.id}/cache/")


def upload(args):
    client, _etags = connect(args)
    try:
        with open(args.file, "rb") as f:
            content = f.read()
    except OSError as exc:
        raise CommandError(f"Can't read {args.file}: {exc}", USAGE) from exc
    output(client.upload(os.path.basename(args.file), content)[1])


def batch(args):
    client, etags = connect(args)
    body = read_json(args.file)
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
    _status, data = client.request("POST", "batch/", params=_params(args), body=body)
    if args.apply:
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
    _written(args, data)


# The parser #################################################################


def build_parser():
    parser = argparse.ArgumentParser(
        prog=prog(),
        description="The feincms3 content API from the command line.",
        epilog="Exit codes: 0 done, 1 failed, 2 usage, 3 invalid data (with"
        " paths), 4 conflict (ETag, references), 5 not found, 6 not allowed.",
    )
    parser.add_argument("--site", help="a site added with `site add`")
    commands = parser.add_subparsers(title="commands")

    def command(name, handler, help):
        sub = commands.add_parser(name, help=help, description=help)
        sub.set_defaults(handler=handler)
        return sub

    site = commands.add_parser("site", help="the sites and their tokens")
    sites = site.add_subparsers(title="site commands")
    add = sites.add_parser("add", help="add a site; asks for the token")
    add.add_argument("name")
    add.add_argument("url", help="where the API is, e.g. https://example.com/api/")
    add.add_argument(
        "--token-stdin", action="store_true", help="read the token from stdin"
    )
    add.set_defaults(handler=site_add)
    sites.add_parser("list", help="the sites").set_defaults(handler=site_list)
    remove = sites.add_parser("remove", help="remove a site")
    remove.add_argument("name")
    remove.set_defaults(handler=site_remove)
    default = sites.add_parser("default", help="use a site by default")
    default.add_argument("name")
    default.set_defaults(handler=site_default)

    command("resources", resources, "the resources")
    sub = command("schema", schema, "the regions and plugins, or one type's fields")
    sub.add_argument("type", nargs="?", help="e.g. pages.page")

    sub = command("list", list_objects, "list objects")
    sub.add_argument("resource")
    sub.add_argument("-f", "--filter", action="append", default=[], metavar="KEY=VALUE")
    sub.add_argument("-q", help="search")
    sub.add_argument("--limit", type=int)
    sub.add_argument("--offset", type=int)
    sub.add_argument("--fields", help="only these fields, comma separated")
    sub.add_argument("--content", action="store_true", help="with content")
    sub.add_argument("--expand", action="store_true", help="describe references")

    sub = command("get", get, "one object")
    sub.add_argument("resource")
    sub.add_argument("id")
    sub.add_argument("--nested", action="store_true", help="containers with children")
    sub.add_argument("--expand", action="store_true", help="describe references")

    sub = command("find", find, "an object by its path")
    sub.add_argument("path")
    sub.add_argument("--resource", default="pages")
    sub.add_argument("--host")

    sub = command("plugin", plugin, "one plugin of an object")
    sub.add_argument("resource")
    sub.add_argument("id")
    sub.add_argument("type")
    sub.add_argument("plugin_id")

    def writing(sub, *, etag=True):
        sub.add_argument(
            "--apply", action="store_true", help="save; without it, a dry run"
        )
        if etag:
            sub.add_argument("--etag", help="instead of the one last read")
            sub.add_argument(
                "--force", action="store_true", help="overwrite whatever is there"
            )
        return sub

    sub = writing(command("create", create, "create an object from JSON"), etag=False)
    sub.add_argument("resource")
    sub.add_argument("file", nargs="?", help="JSON; stdin if left out or -")

    sub = writing(command("update", update, "change fields, replace regions"))
    sub.add_argument("resource")
    sub.add_argument("id")
    sub.add_argument("file", nargs="?", help="JSON; stdin if left out or -")

    sub = writing(command("content", content, "replace the regions given"))
    sub.add_argument("resource")
    sub.add_argument("id")
    sub.add_argument("file", nargs="?", help="JSON; stdin if left out or -")

    sub = writing(command("update-plugin", update_plugin, "change one plugin's fields"))
    sub.add_argument("resource")
    sub.add_argument("id")
    sub.add_argument("type")
    sub.add_argument("plugin_id")
    sub.add_argument("file", nargs="?", help="JSON; stdin if left out or -")

    sub = writing(command("delete", delete, "delete an object"))
    sub.add_argument("resource")
    sub.add_argument("id")

    sub = command("cache-clear", cache_clear, "delete an object's cached rendering")
    sub.add_argument("resource")
    sub.add_argument("id")

    sub = command("upload", upload, "stage a file; prints the reference to write")
    sub.add_argument("file")

    sub = writing(
        command("batch", batch, "several writes in one transaction"), etag=False
    )
    sub.add_argument("file", nargs="?", help="JSON; stdin if left out or -")
    return parser


def main_exit():
    sys.exit(main())
