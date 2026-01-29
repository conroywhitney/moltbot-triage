#!/usr/bin/env python3
"""
aggregate.py — Read state files + votes, generate ranked reports and stats.

Outputs:
  - aggregated/top-issues.md
  - aggregated/top-prs.md
  - aggregated/stats.md
"""

import os
import sys
import re
import yaml
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter, defaultdict

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = BASE_DIR / "state"
VOTES_DIR = BASE_DIR / "votes"
AGG_DIR = BASE_DIR / "aggregated"

AGG_DIR.mkdir(exist_ok=True)

NOW = datetime.now(timezone.utc)
STALE_DAYS = 7


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
        # Also capture the body (everything after second ---)
        data["_body"] = text[end + 3:].strip()
        return data
    except yaml.YAMLError as e:
        log(f"  YAML error in {filepath}: {e}")
        return None


def parse_date(s):
    """Parse ISO8601 date string."""
    if not s:
        return None
    try:
        if isinstance(s, datetime):
            if s.tzinfo is None:
                return s.replace(tzinfo=timezone.utc)
            return s
        s = str(s).replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def days_ago(dt):
    if dt is None:
        return 9999
    return (NOW - dt).days


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


PRIORITY_SCORES = {
    "critical": 10,
    "high": 7,
    "medium": 4,
    "low": 2,
}


def load_votes():
    """Load all vote files. Returns dict: issue_number -> list of {agent, priority, reason, ...}.
    
    Supports two layouts:
    1. Per-issue files: votes/{number}/{agent}.yml with flat keys (agent, issue, priority, reason)
    2. Nested dicts: votes/{agent}.yml with issues: {number: {priority, reason}}
    """
    votes = defaultdict(list)
    if not VOTES_DIR.exists():
        return votes

    for vote_dir in VOTES_DIR.iterdir():
        if not vote_dir.is_dir():
            continue
        
        # Try to interpret dir name as issue number (layout 1: votes/{number}/{agent}.yml)
        try:
            issue_number = int(vote_dir.name)
        except ValueError:
            issue_number = None

        for vote_file in vote_dir.glob("*.yml"):
            try:
                data = yaml.safe_load(vote_file.read_text())
            except Exception:
                continue
            if not isinstance(data, dict):
                continue

            agent = vote_file.stem

            if issue_number is not None:
                # Layout 1: flat per-issue file
                priority_raw = data.get("priority", "medium")
                if isinstance(priority_raw, str):
                    priority = PRIORITY_SCORES.get(priority_raw.lower(), 4)
                else:
                    priority = int(priority_raw)
                
                votes[issue_number].append({
                    "agent": agent,
                    "priority": priority,
                    "priority_label": str(priority_raw),
                    "reason": data.get("reason", "").strip(),
                    "willing_to_work": data.get("willing_to_work", False),
                    "status": data.get("status", ""),
                    "related_prs": data.get("related_prs", []),
                    "duplicate_of": data.get("duplicate_of", None),
                })
            else:
                # Layout 2: nested dict
                issue_votes = data.get("issues", data.get("votes", {}))
                if isinstance(issue_votes, dict):
                    for num, info in issue_votes.items():
                        num = int(num) if str(num).isdigit() else num
                        if isinstance(info, dict):
                            votes[num].append({
                                "agent": agent,
                                "priority": info.get("priority", 0),
                                "reason": info.get("reason", ""),
                            })
    return votes


def vote_score(number, votes_map):
    """Calculate vote score: sum of priorities * voter count multiplier."""
    v = votes_map.get(number, [])
    if not v:
        return 0
    total_priority = sum(x["priority"] for x in v)
    voter_count = len(v)
    return total_priority * (1 + 0.5 * (voter_count - 1))


def generate_top_issues(issues, votes_map):
    """Generate aggregated/top-issues.md."""
    log("Generating top-issues.md...")

    scored = []
    for issue in issues:
        num = issue["number"]
        score = vote_score(num, votes_map)
        reactions = issue.get("reactions_total", 0) or 0
        comments = issue.get("comments_count", 0) or 0
        # Composite: vote score dominates, then reactions, then comments
        composite = score * 100 + reactions * 5 + comments * 2
        scored.append((composite, score, issue))

    scored.sort(key=lambda x: x[0], reverse=True)

    lines = [
        "# Top Issues — Ranked",
        "",
        f"_Generated: {NOW.strftime('%Y-%m-%d %H:%M UTC')}_",
        f"_Total open issues: {len(issues)}_",
        "",
    ]

    # Voted issues first
    voted = [(c, s, i) for c, s, i in scored if s > 0]
    if voted:
        lines.append("## 🗳️ Voted Issues")
        lines.append("")
        lines.append("| # | Score | 👍 | 💬 | Labels | Title |")
        lines.append("|---|-------|----|----|--------|-------|")
        for composite, score, issue in voted:
            num = issue["number"]
            labels = ", ".join(issue.get("labels", []) or [])
            title = issue.get("title", "")[:60]
            reactions = issue.get("reactions_total", 0) or 0
            comments = issue.get("comments_count", 0) or 0
            voters = votes_map.get(num, [])
            voter_names = ", ".join(v["agent"] for v in voters)
            lines.append(f"| [#{num}]({issue.get('url', '')}) | {score:.0f} | {reactions} | {comments} | {labels} | {title} |")
            for v in voters:
                reason = v.get("reason", "")
                if reason:
                    lines.append(f"| | ↳ {v['agent']}: {reason[:80]} | | | | |")
        lines.append("")

    # Top by engagement (non-voted)
    lines.append("## 🔥 Top by Engagement (no votes yet)")
    lines.append("")
    lines.append("| # | 👍 | 💬 | Labels | Title |")
    lines.append("|---|----|----|--------|-------|")
    count = 0
    for composite, score, issue in scored:
        if score > 0:
            continue
        num = issue["number"]
        labels = ", ".join(issue.get("labels", []) or [])
        title = issue.get("title", "")[:60]
        reactions = issue.get("reactions_total", 0) or 0
        comments = issue.get("comments_count", 0) or 0
        if reactions == 0 and comments == 0:
            continue
        lines.append(f"| [#{num}]({issue.get('url', '')}) | {reactions} | {comments} | {labels} | {title} |")
        count += 1
        if count >= 30:
            break
    lines.append("")

    (AGG_DIR / "top-issues.md").write_text("\n".join(lines), encoding="utf-8")
    log(f"  Wrote top-issues.md ({len(voted)} voted, {count} by engagement)")


def generate_top_prs(prs, votes_map):
    """Generate aggregated/top-prs.md."""
    log("Generating top-prs.md...")

    scored = []
    for pr in prs:
        num = pr["number"]
        # Score based on: fixes voted issues, then size/age/reviews
        fixes = pr.get("fixes_issues", []) or []
        fix_score = sum(vote_score(f, votes_map) for f in fixes)
        
        vote_s = vote_score(num, votes_map)
        reactions = pr.get("reactions_total", 0) or 0
        comments = pr.get("comments_count", 0) or 0
        
        composite = (fix_score * 200) + (vote_s * 100) + reactions * 5 + comments * 2
        scored.append((composite, fix_score, vote_s, pr))

    scored.sort(key=lambda x: x[0], reverse=True)

    lines = [
        "# Top PRs — Ranked",
        "",
        f"_Generated: {NOW.strftime('%Y-%m-%d %H:%M UTC')}_",
        f"_Total open PRs: {len(prs)}_",
        "",
    ]

    # PRs that fix voted issues
    fixing = [(c, fs, vs, p) for c, fs, vs, p in scored if fs > 0]
    if fixing:
        lines.append("## 🎯 PRs Fixing Voted Issues (HIGH SIGNAL)")
        lines.append("")
        lines.append("| # | Fixes | Size | CI | Review | Title |")
        lines.append("|---|-------|------|----|--------|-------|")
        for _, fs, _, pr in fixing:
            num = pr["number"]
            fixes = pr.get("fixes_issues", [])
            fix_str = ", ".join(f"#{f}" for f in fixes)
            size = pr.get("size", "?")
            ci = pr.get("ci_status", "?")
            review = pr.get("review_decision", "none")
            title = pr.get("title", "")[:50]
            lines.append(f"| [#{num}]({pr.get('url', '')}) | {fix_str} | {size} | {ci} | {review} | {title} |")
        lines.append("")

    # All PRs by score
    lines.append("## 📋 All PRs by Priority")
    lines.append("")
    lines.append("| # | Score | Size | CI | Review | Draft | Age | Title |")
    lines.append("|---|-------|------|----|--------|-------|-----|-------|")
    for composite, _, _, pr in scored[:50]:
        num = pr["number"]
        size = pr.get("size", "?")
        ci = pr.get("ci_status", "?")
        review = pr.get("review_decision", "none")
        draft = "✏️" if pr.get("draft") else ""
        age = days_ago(parse_date(pr.get("created")))
        title = pr.get("title", "")[:50]
        lines.append(f"| [#{num}]({pr.get('url', '')}) | {composite:.0f} | {size} | {ci} | {review} | {draft} | {age}d | {title} |")
    lines.append("")

    (AGG_DIR / "top-prs.md").write_text("\n".join(lines), encoding="utf-8")
    log(f"  Wrote top-prs.md ({len(fixing)} fix voted issues)")


def generate_stats(issues, prs, votes_map):
    """Generate aggregated/stats.md with comprehensive meta stats."""
    log("Generating stats.md...")

    lines = [
        "# Repository Stats",
        "",
        f"_Generated: {NOW.strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
    ]

    # Overview
    lines.append("## Overview")
    lines.append("")
    lines.append(f"- **Open Issues:** {len(issues)}")
    lines.append(f"- **Open PRs:** {len(prs)}")
    draft_prs = [p for p in prs if p.get("draft")]
    lines.append(f"- **Draft PRs:** {len(draft_prs)}")
    voted_issues = [i for i in issues if vote_score(i["number"], votes_map) > 0]
    lines.append(f"- **Voted Issues:** {len(voted_issues)}")
    lines.append("")

    # Issue label distribution
    lines.append("## Issues by Label")
    lines.append("")
    label_counts = Counter()
    for issue in issues:
        for label in (issue.get("labels") or []):
            if isinstance(label, str):
                label_counts[label] += 1
    for label, count in label_counts.most_common(30):
        bar = "█" * min(count, 50)
        lines.append(f"- **{label}**: {count} {bar}")
    lines.append("")

    # PR size distribution
    lines.append("## PR Size Distribution")
    lines.append("")
    size_counts = Counter()
    total_additions = 0
    total_deletions = 0
    for pr in prs:
        size_counts[pr.get("size", "unknown")] += 1
        total_additions += pr.get("additions", 0) or 0
        total_deletions += pr.get("deletions", 0) or 0
    
    size_order = ["tiny", "small", "medium", "large", "huge", "unknown"]
    for size in size_order:
        count = size_counts.get(size, 0)
        if count > 0:
            bar = "█" * min(count, 50)
            lines.append(f"- **{size}** (<10/50/200/1000/>1000 lines): {count} {bar}")
    
    avg_size = (total_additions + total_deletions) / max(len(prs), 1)
    lines.append(f"")
    lines.append(f"- **Average PR size:** {avg_size:.0f} lines ({total_additions}+ / {total_deletions}-)")
    lines.append("")

    # Huge PRs (>1000 lines)
    huge_prs = [p for p in prs if (p.get("additions", 0) or 0) + (p.get("deletions", 0) or 0) > 1000]
    if huge_prs:
        lines.append("## 🐘 Huge PRs (>1000 lines changed)")
        lines.append("")
        lines.append("| # | Lines | Files | Author | Title |")
        lines.append("|---|-------|-------|--------|-------|")
        huge_prs.sort(key=lambda p: (p.get("additions", 0) or 0) + (p.get("deletions", 0) or 0), reverse=True)
        for pr in huge_prs:
            num = pr["number"]
            total = (pr.get("additions", 0) or 0) + (pr.get("deletions", 0) or 0)
            files = pr.get("changed_files", 0) or 0
            author = pr.get("author", "?")
            title = pr.get("title", "")[:50]
            lines.append(f"| [#{num}]({pr.get('url', '')}) | {total} | {files} | @{author} | {title} |")
        lines.append("")

    # PR age / staleness
    lines.append("## PR Staleness")
    lines.append("")
    stale_prs = []
    for pr in prs:
        updated = parse_date(pr.get("updated"))
        age = days_ago(updated)
        if age > STALE_DAYS:
            stale_prs.append((age, pr))
    stale_prs.sort(key=lambda x: x[0], reverse=True)
    lines.append(f"- **Stale PRs (>{STALE_DAYS} days no activity):** {len(stale_prs)}")
    lines.append("")
    
    if stale_prs:
        lines.append("### Oldest Stale PRs")
        lines.append("")
        lines.append("| # | Days Stale | Size | Author | Title |")
        lines.append("|---|-----------|------|--------|-------|")
        for age, pr in stale_prs[:20]:
            num = pr["number"]
            size = pr.get("size", "?")
            author = pr.get("author", "?")
            title = pr.get("title", "")[:50]
            lines.append(f"| [#{num}]({pr.get('url', '')}) | {age} | {size} | @{author} | {title} |")
        lines.append("")

    # Zero-review PRs
    lines.append("## 👀 PRs with No Reviews")
    lines.append("")
    no_review = [p for p in prs if not (p.get("reviews") or []) and not p.get("draft")]
    lines.append(f"- **Non-draft PRs with zero reviews:** {len(no_review)} / {len(prs)} ({100*len(no_review)/max(len(prs),1):.0f}%)")
    lines.append("")
    if no_review:
        # Sort by age (oldest first)
        no_review.sort(key=lambda p: parse_date(p.get("created")) or NOW)
        lines.append("### Oldest Unreviewed PRs")
        lines.append("")
        lines.append("| # | Age | Size | CI | Author | Title |")
        lines.append("|---|-----|------|----|--------|-------|")
        for pr in no_review[:20]:
            num = pr["number"]
            age = days_ago(parse_date(pr.get("created")))
            size = pr.get("size", "?")
            ci = pr.get("ci_status", "?")
            author = pr.get("author", "?")
            title = pr.get("title", "")[:50]
            lines.append(f"| [#{num}]({pr.get('url', '')}) | {age}d | {size} | {ci} | @{author} | {title} |")
        lines.append("")

    # CI failures
    lines.append("## 🔴 PRs with CI Failures")
    lines.append("")
    ci_fail = [p for p in prs if p.get("ci_status") == "failing"]
    lines.append(f"- **CI failing:** {len(ci_fail)}")
    if ci_fail:
        lines.append("")
        lines.append("| # | Size | Author | Title |")
        lines.append("|---|------|--------|-------|")
        for pr in ci_fail:
            num = pr["number"]
            size = pr.get("size", "?")
            author = pr.get("author", "?")
            title = pr.get("title", "")[:50]
            lines.append(f"| [#{num}]({pr.get('url', '')}) | {size} | @{author} | {title} |")
    lines.append("")

    # Top contributors (by open PR count)
    lines.append("## 👥 Top Contributors (by open PR count)")
    lines.append("")
    author_counts = Counter(p.get("author", "ghost") for p in prs)
    for author, count in author_counts.most_common(20):
        bar = "█" * min(count, 30)
        lines.append(f"- **@{author}**: {count} {bar}")
    lines.append("")

    # PRs that fix voted issues
    lines.append("## 🎯 PRs Fixing Voted Issues")
    lines.append("")
    fixing = []
    for pr in prs:
        fixes = pr.get("fixes_issues", []) or []
        fix_voted = [f for f in fixes if vote_score(f, votes_map) > 0]
        if fix_voted:
            fixing.append((pr, fix_voted))
    if fixing:
        lines.append("| # | Fixes | Author | Title |")
        lines.append("|---|-------|--------|-------|")
        for pr, fix_voted in fixing:
            num = pr["number"]
            fix_str = ", ".join(f"#{f}" for f in fix_voted)
            author = pr.get("author", "?")
            title = pr.get("title", "")[:50]
            lines.append(f"| [#{num}]({pr.get('url', '')}) | {fix_str} | @{author} | {title} |")
    else:
        lines.append("- None detected (no PRs reference voted issues with Fixes/Closes)")
    lines.append("")

    # Duplicate clusters
    lines.append("## 🔄 Potential Duplicate Clusters")
    lines.append("")
    dupes = defaultdict(list)
    for issue in issues:
        dup = issue.get("duplicate_of")
        if dup:
            dupes[dup].append(issue["number"])
    if dupes:
        for original, duplicates in sorted(dupes.items()):
            dup_str = ", ".join(f"#{d}" for d in duplicates)
            lines.append(f"- #{original} ← duplicated by: {dup_str}")
    else:
        lines.append("- None detected from issue body text (manual review needed)")
    lines.append("")

    # PR label distribution
    lines.append("## PR Labels")
    lines.append("")
    pr_label_counts = Counter()
    for pr in prs:
        for label in (pr.get("labels") or []):
            if isinstance(label, str):
                pr_label_counts[label] += 1
    for label, count in pr_label_counts.most_common(20):
        bar = "█" * min(count, 30)
        lines.append(f"- **{label}**: {count} {bar}")
    lines.append("")

    (AGG_DIR / "stats.md").write_text("\n".join(lines), encoding="utf-8")
    log(f"  Wrote stats.md")


def main():
    log("Loading state files...")
    issues = load_all("issues")
    log(f"  Loaded {len(issues)} issues")
    prs = load_all("prs")
    log(f"  Loaded {len(prs)} PRs")

    log("Loading votes...")
    votes_map = load_votes()
    voted_count = sum(1 for v in votes_map.values() if v)
    log(f"  Found votes for {voted_count} items")

    generate_top_issues(issues, votes_map)
    generate_top_prs(prs, votes_map)
    generate_stats(issues, prs, votes_map)

    log("Done!")


if __name__ == "__main__":
    main()
