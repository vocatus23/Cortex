#!/usr/bin/env python3
"""
Cortex Atom Writer -- Creates new memory atoms with standardized frontmatter.

Each atom is a markdown file with YAML frontmatter containing structured
metadata (id, name, type, project, status, dates, tags, links).

Usage:
    python -m cortex.atom_writer --name "Test before deploy" --type rule \
        --project ops --tags "deploy,risk" --body "Always run tests first."
"""
import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from cortex.config import get_vault_path, get_valid_projects, get_valid_types, get_valid_statuses


VAULT = get_vault_path()
VALID_TYPES = get_valid_types()
VALID_STATUSES = get_valid_statuses()


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s-]+', '_', text)
    return text[:60]


def create_atom(name: str, atom_type: str, project: str, tags: list[str],
                body: str, directory: str = None, status: str = "active",
                pnl_impact: float = None, confidence: float = None) -> Path:
    now = datetime.now(timezone.utc)
    date_prefix = now.strftime("%Y%m%d")
    slug = slugify(name)
    atom_id = f"{date_prefix}_{slug}"

    if directory:
        target_dir = (VAULT / directory).resolve()
        if not target_dir.is_relative_to(VAULT.resolve()):
            raise ValueError(f"Directory must be within vault: {directory}")
    else:
        dir_map = {
            "rule": "rules", "feedback": "feedback", "insight": "insights",
            "incident": "incidents", "person": "people", "reference": "reference",
            "user": "user",
        }
        target_dir = VAULT / dir_map.get(atom_type, "atoms")

    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{slug}.md"
    filepath = target_dir / filename

    if filepath.exists():
        counter = 2
        while filepath.exists():
            filepath = target_dir / f"{slug}_{counter}.md"
            counter += 1

    fm_lines = [
        "---",
        f"id: {atom_id}",
        f"name: {name}",
        f"type: {atom_type}",
        f"project: {project}",
        f"status: {status}",
        f"created: {now.strftime('%Y-%m-%d')}",
        f"updated: {now.strftime('%Y-%m-%d')}",
        f"tags: [{', '.join(tags)}]",
        "links: []",
    ]
    if pnl_impact is not None:
        fm_lines.append(f"pnl_impact: {pnl_impact}")
    if confidence is not None:
        fm_lines.append(f"confidence: {confidence}")
    fm_lines.append("---")

    content = "\n".join(fm_lines) + f"\n\n# {name}\n\n{body}\n"
    filepath.write_text(content, encoding="utf-8")
    return filepath


def main():
    valid_projects = get_valid_projects()

    parser = argparse.ArgumentParser(description="Create a new Cortex atom")
    parser.add_argument("--name", required=True, help="Atom title")
    parser.add_argument("--type", required=True, choices=VALID_TYPES, help="Atom type")
    parser.add_argument("--project", required=True, help="Project name")
    parser.add_argument("--tags", default="", help="Comma-separated tags")
    parser.add_argument("--body", default="", help="Body text (or - for stdin)")
    parser.add_argument("--dir", default=None, help="Override target directory")
    parser.add_argument("--status", default="active", choices=VALID_STATUSES)
    parser.add_argument("--pnl", type=float, default=None, help="PnL impact")
    parser.add_argument("--confidence", type=float, default=None, help="Confidence 0-1")

    args = parser.parse_args()
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    body = sys.stdin.read() if args.body == "-" else args.body

    path = create_atom(args.name, args.type, args.project, tags, body,
                       args.dir, args.status, args.pnl, args.confidence)
    print(f"Created: {path.relative_to(VAULT)}")


if __name__ == "__main__":
    main()
