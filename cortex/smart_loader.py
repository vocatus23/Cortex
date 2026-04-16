#!/usr/bin/env python3
"""
Cortex Smart Loader -- Query-driven memory retrieval.

Instead of loading the entire vault, loads only atoms relevant to the current
query. Uses multi-dimensional JSON indexes, keyword scoring with temporal
layer bonuses, and status-based filtering.

Scoring algorithm:
    name match:        +10 per keyword
    tag match:          +8 per keyword
    project match:      +5 per keyword
    description match:  +4 per keyword
    path match:         +3 per keyword
    multi-keyword bonus: x1.5 if ALL keywords match
    temporal bonus:     +2 (hot), +1 (warm), +0 (cold)
    status penalty:     x0.3 (archived), x0.5 (superseded)

Usage:
    python -m cortex.smart_loader "deploy risk"           # keyword search
    python -m cortex.smart_loader --project myproject      # filter by project
    python -m cortex.smart_loader --project myproject --hot # only last 48h
    python -m cortex.smart_loader --type rule               # filter by type
    python -m cortex.smart_loader --tag risk --top 5        # top 5 by tag
    python -m cortex.smart_loader --summary                 # vault summary
"""
import argparse
import json
import sys
from datetime import datetime

from cortex.config import get_vault_path, get_index_dir, get_layer_thresholds
from cortex.tracker import log_retrieval


VAULT = get_vault_path()
INDEX_DIR = get_index_dir()


def load_index(name: str) -> dict:
    path = INDEX_DIR / f"{name}.json"
    if not path.exists():
        print(f"Index {name} not found. Run index_builder first.", file=sys.stderr)
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_manifest() -> list[dict]:
    path = INDEX_DIR / "manifest.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def temporal_layer(updated_str: str) -> str:
    """Classify atom into hot/warm/cold based on updated date."""
    thresholds = get_layer_thresholds()
    try:
        updated = datetime.strptime(updated_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return "cold"
    delta = (datetime.now() - updated).days
    if delta <= thresholds["hot"]:
        return "hot"
    elif delta <= thresholds["warm"]:
        return "warm"
    return "cold"


def score_atom(atom: dict, keywords: list[str]) -> float:
    """Score an atom against query keywords. Higher = more relevant."""
    keyword_score = 0.0
    name_lower = atom.get("name", "").lower()
    desc_lower = atom.get("description", "").lower()
    tags = [t.lower() for t in atom.get("tags", [])]
    path_lower = atom.get("path", "").lower()
    project = atom.get("project", "").lower()

    for kw in keywords:
        kw = kw.lower()
        if kw in name_lower:
            keyword_score += 10.0
        if kw in tags:
            keyword_score += 8.0
        if kw == project:
            keyword_score += 5.0
        if kw in desc_lower:
            keyword_score += 4.0
        if kw in path_lower:
            keyword_score += 3.0

    # Multi-keyword bonus: reward atoms matching ALL keywords
    if len(keywords) > 1:
        matched = sum(1 for kw in keywords if kw.lower() in name_lower
                      or kw.lower() in desc_lower or kw.lower() in path_lower
                      or kw.lower() in tags or kw.lower() == project)
        if matched == len(keywords):
            keyword_score *= 1.5

    # Layer bonus as tiebreaker (only if there's already a keyword match)
    if keyword_score > 0:
        layer = temporal_layer(atom.get("updated", ""))
        layer_bonus = {"hot": 2.0, "warm": 1.0, "cold": 0.0}
        keyword_score += layer_bonus.get(layer, 0.0)

    # Status penalty
    if atom.get("status") == "archived":
        keyword_score *= 0.3
    elif atom.get("status") == "superseded":
        keyword_score *= 0.5

    return keyword_score


def search_keywords(manifest: list[dict], keywords: list[str], top_n: int = 10) -> list[dict]:
    """Search manifest by keywords, return top N scored results."""
    flat_keywords = []
    for kw in keywords:
        flat_keywords.extend(kw.split())
    flat_keywords = [k for k in flat_keywords if len(k) > 1]

    scored = []
    for atom in manifest:
        s = score_atom(atom, flat_keywords)
        if s > 0:
            scored.append({**atom, "_score": s, "_layer": temporal_layer(atom.get("updated", ""))})
    scored.sort(key=lambda x: x["_score"], reverse=True)
    return scored[:top_n]


def filter_atoms(manifest: list[dict], project: str = None, atom_type: str = None,
                 tag: str = None, hot_only: bool = False, status: str = "active") -> list[dict]:
    """Filter manifest by structured criteria."""
    results = []
    for atom in manifest:
        if project and atom.get("project") != project:
            continue
        if atom_type and atom.get("type") != atom_type:
            continue
        if tag and tag.lower() not in [t.lower() for t in atom.get("tags", [])]:
            continue
        if status and atom.get("status") != status:
            continue
        layer = temporal_layer(atom.get("updated", ""))
        if hot_only and layer != "hot":
            continue
        results.append({**atom, "_layer": layer})
    return results


def generate_boot_summary(manifest: list[dict]) -> str:
    """Generate a compact boot summary."""
    from collections import Counter

    active = [a for a in manifest if a.get("status") != "archived"]
    hot = [a for a in active if temporal_layer(a.get("updated", "")) == "hot"]
    warm = [a for a in active if temporal_layer(a.get("updated", "")) == "warm"]

    proj_counts = Counter(a["project"] for a in active)
    type_counts = Counter(a["type"] for a in active)

    lines = [
        f"Cortex: {len(active)} active atoms ({len(hot)} hot, {len(warm)} warm)",
        f"Projects: {', '.join(f'{p}({c})' for p, c in proj_counts.most_common(6))}",
        f"Types: {', '.join(f'{t}({c})' for t, c in type_counts.most_common(5))}",
        "",
        "Hot atoms (last 48h):",
    ]
    for a in sorted(hot, key=lambda x: x.get("updated", ""), reverse=True)[:15]:
        lines.append(f"  [{a['project']}] {a['name']}")

    return "\n".join(lines)


def read_atom_content(path: str) -> str:
    """Read full content of an atom."""
    fpath = VAULT / path
    if not fpath.exists():
        return f"[NOT FOUND: {path}]"
    return fpath.read_text(encoding="utf-8", errors="replace")


def format_brief(atom: dict) -> str:
    """Format atom as brief: name + first 3 lines of body."""
    content = read_atom_content(atom["path"])
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            content = content[end + 3:].strip()
    body_lines = [l for l in content.split("\n") if l.strip() and not l.startswith("#")][:3]
    body_preview = " | ".join(body_lines)[:200]
    layer = atom.get("_layer", "?")
    score = atom.get("_score", "")
    score_str = f" (score: {score:.1f})" if score else ""
    return f"[{layer}] {atom['name']}{score_str}\n  {atom['path']}\n  {body_preview}"


def main():
    parser = argparse.ArgumentParser(description="Cortex Smart Loader")
    parser.add_argument("query", nargs="*", help="Keyword search terms")
    parser.add_argument("--project", "-p", help="Filter by project")
    parser.add_argument("--type", "-t", help="Filter by type")
    parser.add_argument("--tag", help="Filter by tag")
    parser.add_argument("--hot", action="store_true", help="Hot layer only")
    parser.add_argument("--top", type=int, default=10, help="Max results")
    parser.add_argument("--paths", action="store_true", help="Output paths only")
    parser.add_argument("--content", action="store_true", help="Output full content")
    parser.add_argument("--brief", action="store_true", help="Output brief summaries")
    parser.add_argument("--summary", action="store_true", help="Boot summary")
    parser.add_argument("--all-status", action="store_true", help="Include archived/superseded")

    args = parser.parse_args()
    manifest = load_manifest()

    if not manifest:
        print("No manifest. Run: python -m cortex.index_builder", file=sys.stderr)
        sys.exit(1)

    if args.summary:
        print(generate_boot_summary(manifest))
        return

    import time
    t0 = time.monotonic()
    if args.query:
        results = search_keywords(manifest, args.query, args.top)
    else:
        status = None if args.all_status else "active"
        results = filter_atoms(manifest, args.project, args.type, args.tag,
                               args.hot, status)[:args.top]
    duration_ms = (time.monotonic() - t0) * 1000
    query_str = " ".join(args.query) if args.query else f"filter:{args.project or ''}/{args.type or ''}/{args.tag or ''}"
    log_retrieval(query_str, results, duration_ms)

    if not results:
        print("No results.", file=sys.stderr)
        return

    if args.content:
        for r in results:
            print(f"\n{'='*60}")
            print(f"# {r['name']} [{r.get('_layer', '?')}]")
            print(f"# {r['path']}")
            print(f"{'='*60}")
            print(read_atom_content(r["path"]))
    elif args.paths:
        for r in results:
            print(r["path"])
    elif args.brief or args.query:
        for r in results:
            print(format_brief(r))
            print()
    else:
        for r in results:
            print(f"  {r['path']}")


if __name__ == "__main__":
    main()
