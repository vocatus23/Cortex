# Cortex

[![CI](https://github.com/vocatus23/Cortex/actions/workflows/ci.yml/badge.svg)](https://github.com/vocatus23/Cortex/actions/workflows/ci.yml)

Hierarchical-temporal retrieval engine for AI agent memory.

Cortex is a file-based memory system that gives LLM agents persistent, queryable memory using plain markdown files. Instead of embedding-based vector search, it uses deterministic keyword scoring with temporal layer bonuses to prioritize recent, relevant context.

**Zero external dependencies.** Python stdlib only. No vector database, no embedding API, no infrastructure.

## The Problem

LLM agents need persistent memory, but current approaches have friction:

- **Full context loading** wastes tokens on irrelevant history. A vault of 500 atoms doesn't fit in a context window, and most of it isn't relevant to the current query.
- **Vector/embedding search** requires API calls, a vector database, and still returns results by semantic similarity rather than operational relevance. An atom from 30 days ago can outrank one from 2 hours ago.
- **Recency matters.** In fast-paced domains (operations, trading, engineering), 80% of queries are about the last 48 hours. Memory systems should reflect this.

Cortex solves these with three ideas: **atoms** (structured markdown), **multi-dimensional indexes** (JSON, not vectors), and **temporal stratification** (hot/warm/cold layers).

## How It Works

```
                    Your Vault
                    (markdown files with YAML frontmatter)
                         |
                         v
              +-----------------------+
              |    Index Builder      |   Scans vault, parses frontmatter,
              |                       |   builds 5 JSON indexes
              +-----------------------+
                         |
          +--------------+--------------+
          |              |              |
          v              v              v
    manifest.json   by_project.json  graph.json
    by_type.json    by_tag.json
          |              |              |
          +--------------+--------------+
                         |
                         v
              +-----------------------+
              |     Smart Loader      |   Keyword scoring + temporal bonus
              |                       |   Returns top-N ranked atoms
              +-----------------------+
                         |
                         v
                  Relevant context
                  (loaded into LLM)
```

## Scoring Algorithm

When you search for keywords, each atom is scored:

| Match Type | Points | Example |
|------------|--------|---------|
| Name match | +10 per keyword | "deploy" in atom name "Deploy freeze" |
| Tag match | +8 per keyword | "risk" in tags ["deploy", "risk"] |
| Project match | +5 per keyword | "ops" matches project field |
| Description match | +4 per keyword | keyword found in description |
| Path match | +3 per keyword | keyword found in file path |

**Bonuses and penalties:**

| Modifier | Effect | Rationale |
|----------|--------|-----------|
| All keywords match | x1.5 | Reward precision over recall |
| Hot atom (<=2 days) | +2.0 | Recent context wins ties |
| Warm atom (2-7 days) | +1.0 | Settled decisions still relevant |
| Cold atom (>7 days) | +0.0 | No bonus, but still searchable |
| Archived | x0.3 | De-prioritize, don't hide |
| Superseded | x0.5 | Partially relevant |

**Example:** Searching for "deploy risk"

```
Atom A: name="Deploy freeze during release windows", tags=[deploy, risk]
  deploy: +10 (name) +8 (tag) = 18
  risk:   +8 (tag) = 8
  Both keywords match: (18 + 8) * 1.5 = 39.0
  Updated 1 day ago (hot): +2.0
  Final score: 41.0

Atom B: name="API rate limits", tags=[api, performance]
  deploy: no match
  risk:   no match
  Final score: 0.0 (not returned)
```

## Quick Start

```bash
# Clone the repository
git clone https://github.com/vocatus23/Cortex.git
cd Cortex

# Try with the example vault
CORTEX_VAULT=examples/vault python3 -m cortex.index_builder
CORTEX_VAULT=examples/vault python3 -m cortex.smart_loader "deploy risk" --brief
```

### Create your own vault

```bash
mkdir -p my-vault/{atoms,rules,feedback,insights,_moc,index,layers}

# Copy the config and customize
cp cortex.toml my-vault/

# Create your first atom
CORTEX_VAULT=my-vault python3 -m cortex.atom_writer \
    --name "First rule" --type rule --project default \
    --tags "process" --body "Document everything."

# Build indexes
CORTEX_VAULT=my-vault python3 -m cortex.index_builder

# Search
CORTEX_VAULT=my-vault python3 -m cortex.smart_loader "rule" --brief
```

## Data Model

Every atom is a markdown file with YAML frontmatter:

```yaml
---
id: 20260412_deploy_freeze_during_release
name: Deploy freeze during release windows
type: decision
project: ops
status: active
created: 2026-04-12
updated: 2026-04-12
tags: [deploy, risk, decision]
links: []
---

# Deploy freeze during release windows

No deploys allowed 24 hours before and after a release cut.

**Why:** Concurrent deploys during the v2.3 release caused a merge conflict
that broke the staging environment for 6 hours.

**How to apply:** Check the release calendar before scheduling any deploy.
```

### Frontmatter Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | string | yes | `YYYYMMDD_slugified_name` (unique) |
| name | string | yes | Human-readable title |
| type | enum | yes | rule, insight, incident, project, person, reference, decision, lesson, event, loss, win, concept, feedback, user |
| project | string | yes | Project name (configured in cortex.toml) |
| status | enum | yes | active, review, archived, superseded |
| created | date | yes | YYYY-MM-DD |
| updated | date | yes | YYYY-MM-DD |
| tags | list | yes | Lowercase keywords |
| links | list | yes | Reserved for wikilink targets |
| description | string | no | First line of body, max 150 chars |
| pnl_impact | float | no | Domain-specific numeric impact |
| confidence | float | no | 0-1 confidence level |

## Temporal Layers

Cortex classifies every atom into three temporal layers based on `updated` date:

```
  Hot   (<=2 days)    29 atoms    Full detail, high priority
  -------- threshold --------
  Warm  (2-7 days)    69 atoms    Medium priority
  -------- threshold --------
  Cold  (>7 days)    372 atoms    Low priority, background

  Total: 470 atoms
  Hot + Warm = 21% of vault, covers the majority of routine queries
```

The thresholds are configurable in `cortex.toml`:

```toml
[layers]
hot = 2    # days
warm = 7   # days
```

The `layer_compressor` module builds `layers/hot.json`, `layers/warm.json`, and `layers/cold.json` for fast layer-specific retrieval.

## MOC Auto-Refresh

Map-of-Content (MOC) files serve as entry points to each project domain. The `moc_refresher` module keeps them current by replacing content between marker pairs:

```markdown
<!-- AUTO:section_name -->
...this content is replaced on each run...
<!-- /AUTO:section_name -->
```

This is a non-destructive update: only content between markers is touched. Manual edits outside markers are preserved. Writes are atomic (temp file + rename).

Built-in collectors update vault atom counts. The module is designed to be extended with custom collectors for your domain (database queries, API calls, computed metrics).

## Modules

| Module | Lines | Purpose |
|--------|-------|---------|
| `smart_loader.py` | 253 | Query engine: keyword scoring + temporal layers |
| `migrate_existing.py` | 277 | Standardize frontmatter on existing files |
| `moc_refresher.py` | 236 | Marker-based MOC auto-update engine |
| `index_builder.py` | 206 | Vault scanner, frontmatter parser, 5 JSON indexes |
| `auto_linker.py` | 181 | Wikilink discovery, orphan/hub analysis |
| `config.py` | 174 | Configuration loader (cortex.toml) |
| `atom_writer.py` | 110 | Create atoms with standardized frontmatter |
| `layer_compressor.py` | 102 | Temporal layer classification (hot/warm/cold) |

Total: ~1,540 lines of Python. No external dependencies.

## Indexes

The index builder produces five JSON files:

| Index | Keys | Purpose |
|-------|------|---------|
| `manifest.json` | flat array | All atom metadata (the master list) |
| `by_project.json` | project names | Atoms grouped by project |
| `by_type.json` | type names | Atoms grouped by type |
| `by_tag.json` | tag names | Atoms grouped by tag |
| `graph.json` | source paths | Directed link graph from wikilinks |

All indexes are human-readable JSON. No binary formats, no database, no server.

## Configuration

Copy `cortex.toml` to your vault root and customize:

```toml
[vault]
path = "/path/to/your/vault"    # or set CORTEX_VAULT env var

[projects]
names = ["ops", "research", "personal"]

[directories]
# Map directory names to project names for auto-inference
feedback = "meta"
rules = "meta"
research = "research"

[tags]
# Keyword-to-tag mapping for auto-tagging
deploy = ["deploy", "commit", "push"]
risk = ["risk", "loss", "halt"]

[layers]
hot = 2     # days
warm = 7    # days
```

Resolution order: `CORTEX_VAULT` env var > `cortex.toml` in cwd > `cortex.toml` in vault root > defaults.

## Cross-Reference Analysis

The auto-linker discovers connections between atoms:

```bash
CORTEX_VAULT=my-vault python3 -m cortex.auto_linker --report
```

Outputs orphans (atoms with no links in or out) and hubs (atoms with the most connections). Useful for identifying knowledge silos and potential cross-references.

## Production Use

Cortex has been deployed since March 2026 managing an operational knowledge base:

- **470 atoms** across 14 projects (3.7 MB vault)
- **292 active atoms**, 178 archived
- **205 unique tags**, 5 JSON indexes (792 KB total)
- **Retrieval latency:** ~80ms end-to-end (50ms manifest load + 30ms scoring)
- **MOC refresh:** runs every 15 minutes, updates 5 files with live metrics
- **Zero downtime** since deployment

## Design Principles

1. **Zero external dependencies.** Stdlib Python only. Runs anywhere Python runs.
2. **Deterministic scoring.** No embeddings, no API calls, no randomness. Same query returns same results.
3. **File-based atoms.** Plain markdown with YAML frontmatter. Works with Obsidian, any text editor, and git.
4. **Temporal relevance over semantic similarity.** Recent atoms win ties because in operational contexts, freshness correlates with relevance.
5. **Human-readable everything.** Indexes are JSON. Atoms are markdown. Config is TOML. No binary formats.

## Requirements

- Python 3.10+ (3.11+ for native TOML parsing; 3.10 uses built-in fallback)
- No pip install required

## License

MIT
