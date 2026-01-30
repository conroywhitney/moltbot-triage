#!/usr/bin/env python3
"""
aggregate.py — Read state files, generate JSON data for GitHub Pages dashboard.

Outputs (in docs/data/):
  - issues.json
  - prs.json
  - stats.json
  - meta.json
"""

import json
import os
import sys
import yaml
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = BASE_DIR / "state"
DOCS_DIR = BASE_DIR / "docs"
DATA_DIR = DOCS_DIR / "data"
CONFIG_PATH = BASE_DIR / "config.yml"

DATA_DIR.mkdir(parents=True, exist_ok=True)

NOW = datetime.now(timezone.utc)

# Load config
with open(CONFIG_PATH) as f:
    CONFIG = yaml.safe_load(f)

PR_SIZES = CONFIG.get("pr_sizes", {"tiny": 10, "small": 50, "medium": 200, "large": 1000})


def log(msg):
    print(f"[aggregate] {msg}", file=sys.stderr)


def parse_frontmatter(filepath):
    """Parse YAML frontmatter from a markdown file."""
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        log(f"  Error reading {filepath}: {e}")
        return None

    if not text.startswith("---"):
        return None

    end = text.find("---", 3)
    if end == -1:
        return None

    try:
        data = yaml.safe_load(text[3:end])
        if not isinstance(data, dict):
            return None
        return data
    except yaml.YAMLError as e:
        log(f"  YAML error in {filepath}: {e}")
        return None


def load_all(subdir):
    """Load all .md files from state/{subdir}/."""
    items = []
    d = STATE_DIR / subdir
    if not d.exists():
        log(f"  Directory not found: {d}")
        return items
    for f in sorted(d.glob("*.md")):
        data = parse_frontmatter(f)
        if data and "number" in data:
            items.append(data)
    return items


def serialize_date(val):
    """Convert datetime to ISO string, pass through strings."""
    if isinstance(val, datetime):
        return val.isoformat().replace("+00:00", "Z")
    return str(val) if val else None


def generate_issues_json(issues):
    """Generate docs/data/issues.json."""
    log("Generating issues.json...")
    result = []
    for issue in issues:
        result.append({
            "number": issue.get("number"),
            "title": issue.get("title", ""),
            "author": issue.get("author", ""),
            "created": serialize_date(issue.get("created")),
            "updated": serialize_date(issue.get("updated")),
            "labels": issue.get("labels", []) or [],
            "comments_count": issue.get("comments_count", 0) or 0,
            "reactions_total": issue.get("reactions_total", 0) or 0,
            "url": issue.get("url", ""),
            "duplicate_of": issue.get("duplicate_of"),
            "related_issues": issue.get("related_issues", []) or [],
        })
    
    out = DATA_DIR / "issues.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"  Wrote issues.json ({len(result)} issues)")
    return result


def generate_prs_json(prs):
    """Generate docs/data/prs.json."""
    log("Generating prs.json...")
    result = []
    for pr in prs:
        result.append({
            "number": pr.get("number"),
            "title": pr.get("title", ""),
            "author": pr.get("author", ""),
            "created": serialize_date(pr.get("created")),
            "updated": serialize_date(pr.get("updated")),
            "labels": pr.get("labels", []) or [],
            "additions": pr.get("additions", 0) or 0,
            "deletions": pr.get("deletions", 0) or 0,
            "changed_files": pr.get("changed_files", 0) or 0,
            "size": pr.get("size", "unknown"),
            "review_decision": pr.get("review_decision", "none") or "none",
            "reviews": pr.get("reviews", []) or [],
            "ci_status": pr.get("ci_status", "unknown") or "unknown",
            "mergeable": str(pr.get("mergeable", "unknown")),
            "draft": bool(pr.get("draft", False)),
            "comments_count": pr.get("comments_count", 0) or 0,
            "reactions_total": pr.get("reactions_total", 0) or 0,
            "url": pr.get("url", ""),
            "fixes_issues": pr.get("fixes_issues", []) or [],
            "related_prs": pr.get("related_prs", []) or [],
        })
    
    out = DATA_DIR / "prs.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"  Wrote prs.json ({len(result)} PRs)")
    return result


def generate_stats_json(issues_data, prs_data):
    """Generate docs/data/stats.json."""
    log("Generating stats.json...")
    
    total_issues = len(issues_data)
    total_prs = len(prs_data)
    draft_prs = sum(1 for p in prs_data if p.get("draft"))
    
    no_review = [p for p in prs_data if not (p.get("reviews") or []) and not p.get("draft")]
    zero_review_pct = round(100 * len(no_review) / max(total_prs, 1))
    
    ci_fail = [p for p in prs_data if p.get("ci_status") == "failing"]
    ci_failing_pct = round(100 * len(ci_fail) / max(total_prs, 1))
    
    total_additions = sum(p.get("additions", 0) for p in prs_data)
    total_deletions = sum(p.get("deletions", 0) for p in prs_data)
    avg_pr_size = round((total_additions + total_deletions) / max(total_prs, 1))
    
    # Label distributions
    label_counts = Counter()
    for issue in issues_data:
        for label in (issue.get("labels") or []):
            if isinstance(label, str):
                label_counts[label] += 1
    
    pr_label_counts = Counter()
    for pr in prs_data:
        for label in (pr.get("labels") or []):
            if isinstance(label, str):
                pr_label_counts[label] += 1
    
    # Size distribution
    size_counts = Counter(p.get("size", "unknown") for p in prs_data)
    
    # Top contributors
    author_counts = Counter(p.get("author", "ghost") for p in prs_data)
    top_contributors = [{"author": a, "count": c} for a, c in author_counts.most_common(20)]
    
    stats = {
        "total_issues": total_issues,
        "total_prs": total_prs,
        "draft_prs": draft_prs,
        "zero_review_pct": zero_review_pct,
        "ci_failing_pct": ci_failing_pct,
        "avg_pr_size": avg_pr_size,
        "label_distribution": dict(label_counts.most_common(30)),
        "pr_label_distribution": dict(pr_label_counts.most_common(20)),
        "size_distribution": dict(size_counts),
        "top_contributors": top_contributors,
    }
    
    out = DATA_DIR / "stats.json"
    out.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"  Wrote stats.json")
    return stats


def generate_meta_json(issues_data, prs_data):
    """Generate docs/data/meta.json."""
    log("Generating meta.json...")
    
    owner = CONFIG.get("sync", {}).get("owner", "openclaw")
    repo = CONFIG.get("sync", {}).get("repo", "openclaw")
    
    meta = {
        "generated": NOW.isoformat().replace("+00:00", "Z"),
        "issue_count": len(issues_data),
        "pr_count": len(prs_data),
        "repo": f"{owner}/{repo}",
    }
    
    out = DATA_DIR / "meta.json"
    out.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"  Wrote meta.json")
    return meta


def main():
    log("Loading state files...")
    issues = load_all("issues")
    log(f"  Loaded {len(issues)} issues")
    prs = load_all("prs")
    log(f"  Loaded {len(prs)} PRs")

    issues_data = generate_issues_json(issues)
    prs_data = generate_prs_json(prs)
    generate_stats_json(issues_data, prs_data)
    generate_meta_json(issues_data, prs_data)

    log("Done!")


if __name__ == "__main__":
    main()
