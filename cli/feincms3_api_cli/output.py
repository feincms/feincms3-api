"""JSON for scripts and agents, tables for people"""

import json
import sys

import click
from rich import box
from rich.console import Console
from rich.table import Table

from feincms3_api_cli.config import prog


# Both write to sys.stdout and sys.stderr as they are when printing.
# Messages aren't wrapped, so they can be searched for.
console = Console(markup=False, highlight=False)
errors = Console(stderr=True, markup=False, highlight=False, soft_wrap=True)


json_option = click.option(
    "--json", "as_json", is_flag=True, help="JSON, also when printing to a terminal"
)


def message(text):
    errors.print(f"{prog()}: {text}")


def output(data):
    json.dump(data, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def wants_json(as_json):
    """JSON when asked for, and for scripts and agents"""
    return as_json or not sys.stdout.isatty()


def table(*columns):
    """Columns ending in ">" are right-aligned"""
    result = Table(box=box.SIMPLE_HEAD)
    for column in columns:
        if column.endswith(">"):
            result.add_column(column[:-1], justify="right", no_wrap=True)
        else:
            result.add_column(column)
    return result


def cell(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
