"""
Claude Code skills using f3api, linked into Claude Code's skills

The skills are the directories next to this file. Links point to them
where f3api is installed: the checkout for editable installs, else uv's
environment of the tool, so that upgrading f3api upgrades them too.
"""

import os
from pathlib import Path

import click

from feincms3_api_cli.config import prog
from feincms3_api_cli.output import console, json_option, output, table, wants_json


SKILLS = Path(__file__).parent


def _skills():
    return sorted(path.parent for path in SKILLS.glob("*/SKILL.md"))


def _target(project):
    if project:
        return Path.cwd() / ".claude" / "skills"
    config = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(config) if config else Path.home() / ".claude") / "skills"


def description(skill):
    """
    The ``description`` of the frontmatter: on one line, or folded over
    the indented lines following it (``>``)
    """
    lines = (skill / "SKILL.md").read_text(encoding="utf-8").split("\n")
    end = lines.index("---", 1) if lines[:1] == ["---"] else 0
    words = None
    for line in lines[1:end]:
        if words is None:
            if line.startswith("description:"):
                words = [line.removeprefix("description:").strip().lstrip(">|-")]
        elif line[:1].isspace():
            words.append(line.strip())
        else:
            break
    text = " ".join(word for word in words or () if word)
    if text[:1] in {'"', "'"} and text[-1:] == text[:1]:
        text = text[1:-1]
    return text


def _ours(link):
    """
    Links to skills of an f3api, this one or another (an earlier
    environment, a checkout)
    """
    if not link.is_symlink():
        return False
    target = Path(os.readlink(link))
    return target.parent.name == "skills" and target.parent.parent.name == (
        "feincms3_api_cli"
    )


def _state(link, skill):
    if link.is_symlink():
        if Path(os.readlink(link)) == skill:
            return "linked"
        return f"linked to {os.readlink(link)}"
    if link.exists():
        return "a directory of its own"
    return "not linked"


project_option = click.option(
    "--project",
    is_flag=True,
    help="The skills of the project in this directory (.claude/skills),"
    " not your personal ones.",
)


@click.group()
def skills():
    """Claude Code skills using f3api."""


@skills.command("list")
@project_option
@json_option
def skills_list(project, as_json):
    """The skills and whether Claude Code has them."""
    target = _target(project)
    data = [
        {
            "name": skill.name,
            "description": description(skill),
            "path": str(skill),
            "state": _state(target / skill.name, skill),
        }
        for skill in _skills()
    ]
    if wants_json(as_json):
        output(data)
        return
    rows = table("Skill", "State", "Description")
    # Only the descriptions wrap
    rows.columns[0].no_wrap = rows.columns[1].no_wrap = True
    for row in data:
        rows.add_row(row["name"], row["state"], row["description"])
    console.print(rows)
    if any(row["state"] != "linked" for row in data):
        console.print(
            f"Link them with {prog()} skills install{' --project' * project}."
        )


@skills.command()
@project_option
def install(project):
    """
    Link the skills into Claude Code's skills, again when f3api brings new
    ones (upgrading f3api updates those linked).
    """
    target = _target(project)
    target.mkdir(parents=True, exist_ok=True)
    names = set()
    for skill in _skills():
        names.add(skill.name)
        link = target / skill.name
        if link.exists() and not link.is_symlink():
            console.print(f"{link} is a directory of its own, left alone.")
            continue
        if link.is_symlink() and Path(os.readlink(link)) == skill:
            continue
        link.unlink(missing_ok=True)
        link.symlink_to(skill, target_is_directory=True)
        console.print(f"Linked {skill.name}.")
    # Skills f3api doesn't have anymore, or links to an environment uv removed
    for link in sorted(target.iterdir()):
        if link.name not in names and _ours(link):
            link.unlink()
            console.print(f"Removed {link.name}.")
    console.print(f"The skills are in {target}.")


@skills.command()
@project_option
def uninstall(project):
    """Remove the links to the skills."""
    target = _target(project)
    if target.is_dir():
        for link in sorted(target.iterdir()):
            if _ours(link):
                link.unlink()
                console.print(f"Removed {link.name}.")
