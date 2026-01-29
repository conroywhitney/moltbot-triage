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
CONFIG_PATH = BASE_DIR / "config.yml"

AGG_DIR.mkdir(exist_ok=True)

NOW = datetime.now(timezone.utc)

# Load config
with open(CONFIG_PATH) as f:
    CONFIG = yaml.safe_load(f)

STALE_DAYS = CONFIG.get("staleness_days", 7)
WEIGHTS = CONFIG.get("scoring", {}).get("weights", {})
MULTI_VOTER_BONUS = CONFIG.get("scoring", {}).get("multi_voter_bonus", 0.5)
PR_SIZES = CONFIG.get("pr_sizes", {"tiny": 10, "small": 50, "medium": 200, "large": 1000})
REPORT_LIMITS = CONFIG.get("report_limits", {})
PRIORITY_SCORES = CONFIG.get("priority_scores", {"critical": 10, "high": 7, "medium": 4, "low": 2})


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
    return total_priority * (1 + MULTI_VOTER_BONUS * (voter_count - 1))


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
        composite = (score * WEIGHTS.get("vote", 100)
                     + reactions * WEIGHTS.get("reactions", 5)
                     + comments * WEIGHTS.get("comments", 2))
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
        if count >= REPORT_LIMITS.get("top_engagement", 30):
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
        
        composite = (fix_score * WEIGHTS.get("fixes_voted", 200)
                     + vote_s * WEIGHTS.get("vote", 100)
                     + reactions * WEIGHTS.get("reactions", 5)
                     + comments * WEIGHTS.get("comments", 2))
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
    for composite, _, _, pr in scored[:REPORT_LIMITS.get("top_prs", 50)]:
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
    for label, count in label_counts.most_common(REPORT_LIMITS.get("top_labels", 30)):
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
    huge_threshold = PR_SIZES.get("large", 1000)
    huge_prs = [p for p in prs if (p.get("additions", 0) or 0) + (p.get("deletions", 0) or 0) >= huge_threshold]
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
        for age, pr in stale_prs[:REPORT_LIMITS.get("stale_prs", 20)]:
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
        for pr in no_review[:REPORT_LIMITS.get("unreviewed_prs", 20)]:
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
    for author, count in author_counts.most_common(REPORT_LIMITS.get("top_contributors", 20)):
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
    for label, count in pr_label_counts.most_common(REPORT_LIMITS.get("top_contributors", 20)):
        bar = "█" * min(count, 30)
        lines.append(f"- **{label}**: {count} {bar}")
    lines.append("")

    (AGG_DIR / "stats.md").write_text("\n".join(lines), encoding="utf-8")
    log(f"  Wrote stats.md")


def _html_escape(s):
    """Escape HTML special characters."""
    if not s:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")


def generate_html_dashboard(issues, prs, votes_map):
    """Generate aggregated/index.html — a self-contained HTML dashboard."""
    log("Generating index.html...")

    owner = CONFIG.get("sync", {}).get("owner", "moltbot")
    repo = CONFIG.get("sync", {}).get("repo", "moltbot")
    github_base = f"https://github.com/{owner}/{repo}"

    # ── Compute scored issues ────────────────────────────────────────
    scored_issues = []
    for issue in issues:
        num = issue["number"]
        vs = vote_score(num, votes_map)
        reactions = issue.get("reactions_total", 0) or 0
        comments = issue.get("comments_count", 0) or 0
        composite = (vs * WEIGHTS.get("vote", 100)
                     + reactions * WEIGHTS.get("reactions", 5)
                     + comments * WEIGHTS.get("comments", 2))
        scored_issues.append((composite, vs, issue))
    scored_issues.sort(key=lambda x: x[0], reverse=True)

    voted_issues = [(c, s, i) for c, s, i in scored_issues if s > 0]
    trending_issues = [(c, s, i) for c, s, i in scored_issues
                       if s == 0 and ((i.get("reactions_total", 0) or 0) + (i.get("comments_count", 0) or 0)) > 0]

    # ── Compute scored PRs ───────────────────────────────────────────
    scored_prs = []
    for pr in prs:
        num = pr["number"]
        fixes = pr.get("fixes_issues", []) or []
        fix_score = sum(vote_score(f, votes_map) for f in fixes)
        vs = vote_score(num, votes_map)
        reactions = pr.get("reactions_total", 0) or 0
        comments = pr.get("comments_count", 0) or 0
        composite = (fix_score * WEIGHTS.get("fixes_voted", 200)
                     + vs * WEIGHTS.get("vote", 100)
                     + reactions * WEIGHTS.get("reactions", 5)
                     + comments * WEIGHTS.get("comments", 2))
        scored_prs.append((composite, fix_score, vs, pr))
    scored_prs.sort(key=lambda x: x[0], reverse=True)

    fixing_prs = [(c, fs, vs, p) for c, fs, vs, p in scored_prs if fs > 0]

    # ── Stats ────────────────────────────────────────────────────────
    total_issues = len(issues)
    total_prs = len(prs)
    draft_count = sum(1 for p in prs if p.get("draft"))
    no_review = [p for p in prs if not (p.get("reviews") or []) and not p.get("draft")]
    zero_review_pct = 100 * len(no_review) / max(total_prs, 1)
    ci_fail = [p for p in prs if p.get("ci_status") == "failing"]
    ci_fail_pct = 100 * len(ci_fail) / max(total_prs, 1)

    size_counts = Counter(p.get("size", "unknown") for p in prs)
    author_counts = Counter(p.get("author", "ghost") for p in prs)
    top_authors = author_counts.most_common(REPORT_LIMITS.get("top_contributors", 20))
    max_author_count = top_authors[0][1] if top_authors else 1

    huge_threshold = PR_SIZES.get("large", 1000)
    huge_prs = sorted(
        [p for p in prs if (p.get("additions", 0) or 0) + (p.get("deletions", 0) or 0) >= huge_threshold],
        key=lambda p: (p.get("additions", 0) or 0) + (p.get("deletions", 0) or 0),
        reverse=True,
    )

    # ── Helper: badges ───────────────────────────────────────────────
    def size_badge(size):
        colors = {"tiny": "#6b7280", "small": "#10b981", "medium": "#f59e0b", "large": "#f97316", "huge": "#ef4444"}
        c = colors.get(size, "#6b7280")
        return f'<span class="badge" style="background:{c}">{_html_escape(size)}</span>'

    def ci_dot(status):
        colors = {"passing": "#10b981", "failing": "#ef4444", "pending": "#f59e0b"}
        c = colors.get(status, "#6b7280")
        label = _html_escape(status or "unknown")
        return f'<span class="ci-dot" style="background:{c}" title="{label}"></span>'

    def priority_badge(label):
        colors = {"critical": "#ef4444", "high": "#f97316", "medium": "#f59e0b", "low": "#3b82f6"}
        c = colors.get(str(label).lower(), "#6b7280")
        return f'<span class="badge" style="background:{c}">{_html_escape(label)}</span>'

    def pr_link(pr):
        num = pr["number"]
        url = pr.get("url", f"{github_base}/pull/{num}")
        return f'<a href="{_html_escape(url)}" target="_blank">#{num}</a>'

    def issue_link(issue):
        num = issue["number"]
        url = issue.get("url", f"{github_base}/issues/{num}")
        return f'<a href="{_html_escape(url)}" target="_blank">#{num}</a>'

    def issue_link_num(num):
        return f'<a href="{github_base}/issues/{num}" target="_blank">#{num}</a>'

    def age_str(dt_str):
        d = days_ago(parse_date(dt_str))
        if d >= 9999:
            return "?"
        return f"{d}d"

    # ── Section: Review These First ──────────────────────────────────
    review_cards = ""
    for _, fs, vs, pr in fixing_prs:
        num = pr["number"]
        fixes = pr.get("fixes_issues", []) or []
        fix_links = ", ".join(issue_link_num(f) for f in fixes)
        title = _html_escape(pr.get("title", ""))
        author = _html_escape(pr.get("author", "?"))
        age = age_str(pr.get("created"))
        review_cards += f"""
        <div class="action-card">
          <div class="action-card-header">
            {pr_link(pr)} {size_badge(pr.get('size','?'))} {ci_dot(pr.get('ci_status',''))}
          </div>
          <div class="action-card-title">{title}</div>
          <div class="action-card-meta">
            <span>🔧 Fixes: {fix_links}</span>
            <span>👤 @{author}</span>
            <span>⏱️ {age}</span>
          </div>
        </div>"""

    if not fixing_prs:
        review_cards = '<div class="empty-state">No PRs currently fix voted issues. Vote on issues to surface signal!</div>'

    # ── Section: Community Priorities ────────────────────────────────
    priority_cards = ""
    for composite, vs, issue in voted_issues:
        num = issue["number"]
        title = _html_escape(issue.get("title", ""))
        voters = votes_map.get(num, [])
        voter_count = len(voters)
        # Determine highest priority label among voters
        best_label = "medium"
        best_score = 0
        for v in voters:
            if v["priority"] > best_score:
                best_score = v["priority"]
                best_label = v.get("priority_label", "medium")

        voter_html = ""
        for v in voters:
            reason = _html_escape(v.get("reason", "No reason given"))
            agent = _html_escape(v.get("agent", "?"))
            plabel = v.get("priority_label", "medium")
            voter_html += f"""
            <div class="voter-entry">
              <strong>{agent}</strong> {priority_badge(plabel)}
              <div class="voter-reason">{reason}</div>
            </div>"""

        collapse_id = f"voters-{num}"
        priority_cards += f"""
        <div class="priority-card">
          <div class="priority-card-header">
            {issue_link(issue)} {priority_badge(best_label)}
            <span class="voter-count">🗳️ {voter_count} vote{"s" if voter_count != 1 else ""}</span>
          </div>
          <div class="priority-card-title">{title}</div>
          <details class="voter-details">
            <summary>Show voter reasoning</summary>
            {voter_html}
          </details>
        </div>"""

    if not voted_issues:
        priority_cards = '<div class="empty-state">No votes yet. Be the first to vote!</div>'

    # ── Section: Trending ────────────────────────────────────────────
    trending_html = ""
    limit = REPORT_LIMITS.get("top_engagement", 30)
    for i, (composite, vs, issue) in enumerate(trending_issues[:limit]):
        num = issue["number"]
        title = _html_escape(issue.get("title", ""))
        title_short = _html_escape(issue.get("title", "")[:70])
        reactions = issue.get("reactions_total", 0) or 0
        comments = issue.get("comments_count", 0) or 0
        engagement = reactions + comments
        trending_html += f"""
        <div class="trending-item">
          <div class="trending-engagement">
            <span class="engagement-pill">👍 {reactions}</span>
            <span class="engagement-pill">💬 {comments}</span>
          </div>
          <div class="trending-title" title="{title}">
            {issue_link(issue)} {title_short}{"…" if len(issue.get("title","")) > 70 else ""}
          </div>
        </div>"""

    if not trending_issues:
        trending_html = '<div class="empty-state">No trending issues found.</div>'

    # ── Section: All PRs table ───────────────────────────────────────
    all_pr_rows = ""
    for composite, fs, vs, pr in scored_prs[:REPORT_LIMITS.get("top_prs", 50)]:
        num = pr["number"]
        title = _html_escape(pr.get("title", ""))
        size = pr.get("size", "unknown")
        ci = pr.get("ci_status", "unknown")
        review = pr.get("review_decision", "none") or "none"
        draft = "Yes" if pr.get("draft") else "No"
        age = days_ago(parse_date(pr.get("created")))
        author = _html_escape(pr.get("author", "?"))
        additions = pr.get("additions", 0) or 0
        deletions = pr.get("deletions", 0) or 0
        total_lines = additions + deletions
        all_pr_rows += f"""
        <tr data-size="{_html_escape(size)}" data-ci="{_html_escape(ci)}" data-review="{_html_escape(review)}" data-draft="{_html_escape(draft)}">
          <td>{pr_link(pr)}</td>
          <td class="title-cell" title="{title}">{title[:60]}{"…" if len(pr.get("title",""))>60 else ""}</td>
          <td data-sort="{total_lines}">{size_badge(size)}</td>
          <td data-sort="{ci}">{ci_dot(ci)}</td>
          <td>{_html_escape(review)}</td>
          <td>{draft}</td>
          <td data-sort="{age}">{age}d</td>
          <td>@{author}</td>
        </tr>"""

    # ── Section: Health / Stats ──────────────────────────────────────
    size_order = ["tiny", "small", "medium", "large", "huge"]
    size_bar_max = max(size_counts.values()) if size_counts else 1
    size_bars_html = ""
    for s in size_order:
        cnt = size_counts.get(s, 0)
        pct = 100 * cnt / max(size_bar_max, 1)
        colors = {"tiny": "#6b7280", "small": "#10b981", "medium": "#f59e0b", "large": "#f97316", "huge": "#ef4444"}
        c = colors.get(s, "#6b7280")
        size_bars_html += f"""
        <div class="bar-row">
          <span class="bar-label">{s}</span>
          <div class="bar-track"><div class="bar-fill" style="width:{pct}%;background:{c}"></div></div>
          <span class="bar-value">{cnt}</span>
        </div>"""

    contrib_bars_html = ""
    for author, cnt in top_authors[:15]:
        pct = 100 * cnt / max(max_author_count, 1)
        contrib_bars_html += f"""
        <div class="bar-row">
          <span class="bar-label">@{_html_escape(author)}</span>
          <div class="bar-track"><div class="bar-fill" style="width:{pct}%;background:#8b5cf6"></div></div>
          <span class="bar-value">{cnt}</span>
        </div>"""

    huge_prs_html = ""
    for pr in huge_prs[:20]:
        num = pr["number"]
        total = (pr.get("additions", 0) or 0) + (pr.get("deletions", 0) or 0)
        author = _html_escape(pr.get("author", "?"))
        title = _html_escape(pr.get("title", "")[:50])
        huge_prs_html += f"""
        <div class="huge-pr">
          {pr_link(pr)} <span class="badge" style="background:#ef4444">{total} lines</span>
          <span class="huge-pr-title">{title}</span>
          <span class="huge-pr-author">@{author}</span>
        </div>"""

    if not huge_prs:
        huge_prs_html = '<div class="empty-state">No huge PRs. 🎉</div>'

    # ── Assemble full HTML ───────────────────────────────────────────
    timestamp = NOW.strftime("%Y-%m-%d %H:%M UTC")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Moltbot Triage Dashboard</title>
<style>
  :root {{
    --bg: #0f0f1a;
    --bg2: #1a1a2e;
    --bg3: #16213e;
    --border: #2a2a4a;
    --text: #e2e8f0;
    --text2: #94a3b8;
    --accent: #818cf8;
    --link: #93c5fd;
  }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Oxygen,Ubuntu,sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    padding: 0;
  }}
  .container {{ max-width:1200px; margin:0 auto; padding:20px; }}
  h1 {{
    font-size:2rem; font-weight:800; margin-bottom:0.5rem;
    background: linear-gradient(135deg, #818cf8, #c084fc);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    background-clip: text;
  }}
  .subtitle {{ color:var(--text2); font-size:0.95rem; margin-bottom:2rem; }}
  h2 {{
    font-size:1.4rem; font-weight:700; margin:2.5rem 0 1rem;
    padding-bottom:0.5rem; border-bottom:2px solid var(--border);
  }}
  a {{ color:var(--link); text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}

  /* Badges */
  .badge {{
    display:inline-block; padding:2px 10px; border-radius:12px;
    font-size:0.75rem; font-weight:600; color:#fff;
    vertical-align:middle; white-space:nowrap;
  }}
  .ci-dot {{
    display:inline-block; width:10px; height:10px; border-radius:50%;
    vertical-align:middle;
  }}

  /* Action cards */
  .action-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(340px,1fr)); gap:16px; }}
  .action-card {{
    background:var(--bg3); border:1px solid var(--border); border-radius:12px;
    padding:20px; transition:border-color 0.2s;
    border-left:4px solid #10b981;
  }}
  .action-card:hover {{ border-color:var(--accent); }}
  .action-card-header {{ display:flex; align-items:center; gap:8px; margin-bottom:8px; }}
  .action-card-header a {{ font-size:1.1rem; font-weight:700; }}
  .action-card-title {{ font-size:0.95rem; color:var(--text); margin-bottom:10px; }}
  .action-card-meta {{ display:flex; gap:16px; font-size:0.8rem; color:var(--text2); flex-wrap:wrap; }}

  /* Priority cards */
  .priority-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(380px,1fr)); gap:14px; }}
  .priority-card {{
    background:var(--bg2); border:1px solid var(--border); border-radius:10px;
    padding:16px; transition:border-color 0.2s;
  }}
  .priority-card:hover {{ border-color:var(--accent); }}
  .priority-card-header {{ display:flex; align-items:center; gap:8px; margin-bottom:6px; }}
  .priority-card-header a {{ font-size:1rem; font-weight:700; }}
  .priority-card-title {{ font-size:0.9rem; color:var(--text); margin-bottom:8px; }}
  .voter-count {{ font-size:0.8rem; color:var(--text2); margin-left:auto; }}
  .voter-details {{ margin-top:8px; }}
  .voter-details summary {{
    cursor:pointer; font-size:0.8rem; color:var(--text2);
    padding:4px 0;
  }}
  .voter-details summary:hover {{ color:var(--link); }}
  .voter-entry {{
    padding:8px 12px; margin-top:6px;
    background:var(--bg); border-radius:6px; font-size:0.85rem;
  }}
  .voter-reason {{ color:var(--text2); margin-top:4px; font-size:0.8rem; }}

  /* Trending */
  .trending-list {{ display:flex; flex-direction:column; gap:6px; }}
  .trending-item {{
    display:flex; align-items:center; gap:12px;
    padding:10px 14px; background:var(--bg2); border-radius:8px;
    border:1px solid var(--border); transition:border-color 0.2s;
  }}
  .trending-item:hover {{ border-color:var(--accent); }}
  .trending-engagement {{ display:flex; gap:6px; flex-shrink:0; }}
  .engagement-pill {{
    font-size:0.75rem; padding:2px 8px; border-radius:10px;
    background:var(--bg3); color:var(--text2); white-space:nowrap;
  }}
  .trending-title {{ font-size:0.9rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}

  /* Table */
  .table-controls {{
    display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px;
    align-items:center;
  }}
  .filter-group {{ display:flex; gap:4px; flex-wrap:wrap; }}
  .filter-btn {{
    padding:4px 12px; border-radius:16px; border:1px solid var(--border);
    background:var(--bg2); color:var(--text2); cursor:pointer;
    font-size:0.8rem; transition:all 0.2s;
  }}
  .filter-btn:hover {{ border-color:var(--accent); color:var(--text); }}
  .filter-btn.active {{ background:var(--accent); color:#fff; border-color:var(--accent); }}
  .search-box {{
    padding:6px 14px; border-radius:16px; border:1px solid var(--border);
    background:var(--bg2); color:var(--text); font-size:0.85rem;
    outline:none; width:200px;
  }}
  .search-box:focus {{ border-color:var(--accent); }}

  table {{
    width:100%; border-collapse:collapse; font-size:0.85rem;
  }}
  thead {{ position:sticky; top:0; }}
  th {{
    background:var(--bg3); padding:10px 12px; text-align:left;
    border-bottom:2px solid var(--border); cursor:pointer;
    user-select:none; white-space:nowrap;
  }}
  th:hover {{ color:var(--accent); }}
  th .sort-arrow {{ font-size:0.7rem; margin-left:4px; }}
  td {{
    padding:8px 12px; border-bottom:1px solid var(--border);
    vertical-align:middle;
  }}
  .title-cell {{ max-width:300px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  tr:hover td {{ background:var(--bg3); }}
  tr.hidden {{ display:none; }}

  /* Health stats */
  .stats-grid {{
    display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr));
    gap:14px; margin-bottom:24px;
  }}
  .stat-card {{
    background:var(--bg2); border:1px solid var(--border); border-radius:10px;
    padding:16px; text-align:center;
  }}
  .stat-value {{ font-size:1.8rem; font-weight:800; color:var(--accent); }}
  .stat-label {{ font-size:0.8rem; color:var(--text2); margin-top:4px; }}

  .health-section {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; }}
  @media (max-width:768px) {{ .health-section {{ grid-template-columns:1fr; }} }}

  .bar-chart {{ display:flex; flex-direction:column; gap:8px; }}
  .bar-row {{ display:flex; align-items:center; gap:8px; }}
  .bar-label {{ width:80px; font-size:0.8rem; color:var(--text2); text-align:right; flex-shrink:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  .bar-track {{ flex:1; height:20px; background:var(--bg); border-radius:4px; overflow:hidden; }}
  .bar-fill {{ height:100%; border-radius:4px; transition:width 0.3s; min-width:2px; }}
  .bar-value {{ width:30px; font-size:0.8rem; color:var(--text2); }}

  .huge-pr {{
    display:flex; align-items:center; gap:8px; padding:8px 0;
    border-bottom:1px solid var(--border); font-size:0.85rem;
    flex-wrap:wrap;
  }}
  .huge-pr-title {{ color:var(--text2); }}
  .huge-pr-author {{ color:var(--text2); font-size:0.8rem; margin-left:auto; }}

  .empty-state {{
    padding:24px; text-align:center; color:var(--text2);
    background:var(--bg2); border-radius:10px; border:1px dashed var(--border);
  }}

  footer {{
    margin-top:3rem; padding:1.5rem 0; border-top:1px solid var(--border);
    text-align:center; font-size:0.8rem; color:var(--text2);
  }}

  @media (max-width:600px) {{
    .container {{ padding:12px; }}
    h1 {{ font-size:1.4rem; }}
    .action-grid, .priority-grid {{ grid-template-columns:1fr; }}
    .stats-grid {{ grid-template-columns:repeat(2,1fr); }}
    .bar-label {{ width:60px; font-size:0.7rem; }}
    .table-controls {{ flex-direction:column; }}
    .search-box {{ width:100%; }}
  }}
</style>
</head>
<body>
<div class="container">
  <h1>🔮 Moltbot Triage Dashboard</h1>
  <p class="subtitle">{owner}/{repo} &middot; {total_issues} issues &middot; {total_prs} PRs &middot; Generated {timestamp}</p>

  <!-- 🎯 REVIEW THESE FIRST -->
  <h2>🎯 Action: Review These First</h2>
  <p style="color:var(--text2);margin-bottom:14px;font-size:0.9rem;">PRs that fix community-voted issues — the highest signal in this repo.</p>
  <div class="action-grid">
    {review_cards}
  </div>

  <!-- 🗳️ COMMUNITY PRIORITIES -->
  <h2>🗳️ Community Priorities</h2>
  <p style="color:var(--text2);margin-bottom:14px;font-size:0.9rem;">Issues ranked by vote score. Click to expand voter reasoning.</p>
  <div class="priority-grid">
    {priority_cards}
  </div>

  <!-- 🔥 TRENDING -->
  <h2>🔥 Trending (No Votes Yet)</h2>
  <p style="color:var(--text2);margin-bottom:14px;font-size:0.9rem;">High-engagement issues without votes — consider voting on these.</p>
  <div class="trending-list">
    {trending_html}
  </div>

  <!-- 📋 ALL PRs -->
  <h2>📋 All PRs</h2>
  <div class="table-controls">
    <input type="text" class="search-box" id="pr-search" placeholder="🔍 Search titles..." oninput="filterTable()">
    <div class="filter-group" id="size-filters">
      <button class="filter-btn active" data-filter="size" data-value="all" onclick="toggleFilter(this,'size')">All Sizes</button>
      <button class="filter-btn" data-filter="size" data-value="tiny" onclick="toggleFilter(this,'size')">tiny</button>
      <button class="filter-btn" data-filter="size" data-value="small" onclick="toggleFilter(this,'size')">small</button>
      <button class="filter-btn" data-filter="size" data-value="medium" onclick="toggleFilter(this,'size')">medium</button>
      <button class="filter-btn" data-filter="size" data-value="large" onclick="toggleFilter(this,'size')">large</button>
      <button class="filter-btn" data-filter="size" data-value="huge" onclick="toggleFilter(this,'size')">huge</button>
    </div>
    <div class="filter-group" id="ci-filters">
      <button class="filter-btn active" data-filter="ci" data-value="all" onclick="toggleFilter(this,'ci')">All CI</button>
      <button class="filter-btn" data-filter="ci" data-value="passing" onclick="toggleFilter(this,'ci')">✅ passing</button>
      <button class="filter-btn" data-filter="ci" data-value="failing" onclick="toggleFilter(this,'ci')">❌ failing</button>
      <button class="filter-btn" data-filter="ci" data-value="pending" onclick="toggleFilter(this,'ci')">⏳ pending</button>
    </div>
  </div>
  <div style="overflow-x:auto;">
  <table id="pr-table">
    <thead>
      <tr>
        <th onclick="sortTable(0,'num')"># <span class="sort-arrow"></span></th>
        <th onclick="sortTable(1,'str')">Title <span class="sort-arrow"></span></th>
        <th onclick="sortTable(2,'num')">Size <span class="sort-arrow"></span></th>
        <th onclick="sortTable(3,'str')">CI <span class="sort-arrow"></span></th>
        <th onclick="sortTable(4,'str')">Review <span class="sort-arrow"></span></th>
        <th onclick="sortTable(5,'str')">Draft <span class="sort-arrow"></span></th>
        <th onclick="sortTable(6,'num')">Age <span class="sort-arrow"></span></th>
        <th onclick="sortTable(7,'str')">Author <span class="sort-arrow"></span></th>
      </tr>
    </thead>
    <tbody>
      {all_pr_rows}
    </tbody>
  </table>
  </div>

  <!-- 📊 REPOSITORY HEALTH -->
  <h2>📊 Repository Health</h2>
  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-value">{total_issues}</div>
      <div class="stat-label">Open Issues</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">{total_prs}</div>
      <div class="stat-label">Open PRs</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">{zero_review_pct:.0f}%</div>
      <div class="stat-label">Zero Reviews</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">{ci_fail_pct:.0f}%</div>
      <div class="stat-label">CI Failing</div>
    </div>
  </div>

  <div class="health-section">
    <div>
      <h3 style="margin-bottom:12px;font-size:1rem;">PR Size Distribution</h3>
      <div class="bar-chart">
        {size_bars_html}
      </div>
    </div>
    <div>
      <h3 style="margin-bottom:12px;font-size:1rem;">Top Contributors</h3>
      <div class="bar-chart">
        {contrib_bars_html}
      </div>
    </div>
  </div>

  <h3 style="margin-top:24px;margin-bottom:12px;font-size:1rem;">🐘 Huge PRs (&gt;{huge_threshold} lines)</h3>
  <div>
    {huge_prs_html}
  </div>

  <footer>
    Generated {timestamp} by <a href="{github_base}" target="_blank">moltbot-triage</a> &middot;
    Data from {total_issues} issues and {total_prs} pull requests
  </footer>
</div>

<script>
// ── Sort ────────────────────────────────────────────────────────────
let sortCol = -1, sortAsc = true;
function sortTable(col, type) {{
  const table = document.getElementById('pr-table');
  const tbody = table.tBodies[0];
  const rows = Array.from(tbody.rows);
  if (sortCol === col) {{ sortAsc = !sortAsc; }} else {{ sortCol = col; sortAsc = true; }}

  rows.sort((a, b) => {{
    let va = a.cells[col].getAttribute('data-sort') || a.cells[col].textContent.trim();
    let vb = b.cells[col].getAttribute('data-sort') || b.cells[col].textContent.trim();
    if (type === 'num') {{
      va = parseFloat(va.replace(/[^0-9.-]/g,'')) || 0;
      vb = parseFloat(vb.replace(/[^0-9.-]/g,'')) || 0;
      return sortAsc ? va - vb : vb - va;
    }}
    return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
  }});
  rows.forEach(r => tbody.appendChild(r));

  // Update arrows
  table.querySelectorAll('.sort-arrow').forEach(s => s.textContent = '');
  table.rows[0].cells[col].querySelector('.sort-arrow').textContent = sortAsc ? '▲' : '▼';
}}

// ── Filters ─────────────────────────────────────────────────────────
let activeFilters = {{ size: 'all', ci: 'all' }};
function toggleFilter(btn, group) {{
  const val = btn.getAttribute('data-value');
  activeFilters[group] = val;
  document.querySelectorAll(`[data-filter="${{group}}"]`).forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  filterTable();
}}

function filterTable() {{
  const search = document.getElementById('pr-search').value.toLowerCase();
  const rows = document.querySelectorAll('#pr-table tbody tr');
  rows.forEach(row => {{
    const size = row.getAttribute('data-size');
    const ci = row.getAttribute('data-ci');
    const title = row.cells[1].textContent.toLowerCase();
    let show = true;
    if (activeFilters.size !== 'all' && size !== activeFilters.size) show = false;
    if (activeFilters.ci !== 'all' && ci !== activeFilters.ci) show = false;
    if (search && !title.includes(search)) show = false;
    row.classList.toggle('hidden', !show);
  }});
}}
</script>
</body>
</html>"""

    out_path = AGG_DIR / "index.html"
    out_path.write_text(html, encoding="utf-8")
    log(f"  Wrote index.html ({len(html)} bytes)")


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
    generate_html_dashboard(issues, prs, votes_map)

    log("Done!")


if __name__ == "__main__":
    main()
