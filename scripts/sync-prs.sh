#!/usr/bin/env bash
# sync-prs.sh — Pull all open PRs from openclaw/openclaw with body, reviews, comments
set -euo pipefail

REPO_DIR="/Users/clawd/openclaw"
STATE_DIR="$(cd "$(dirname "$0")/.." && pwd)/state/prs"
OWNER="openclaw"
REPO="openclaw"
PAGE_SIZE=50  # PRs have more data, use smaller pages

mkdir -p "$STATE_DIR"

log() { echo "[sync-prs] $*" >&2; }

# GraphQL query — no $cursor variable for first page, handled in loop
QUERY_FIRST='
{
  repository(owner: "openclaw", name: "openclaw") {
    pullRequests(first: 50, states: OPEN, orderBy: {field: CREATED_AT, direction: DESC}) {
      totalCount
      nodes {
        number
        title
        body
        author { login }
        createdAt
        updatedAt
        url
        isDraft
        mergeable
        additions
        deletions
        changedFiles
        labels(first: 20) { nodes { name } }
        reviewDecision
        reviews(first: 20) {
          nodes {
            author { login }
            state
            body
            createdAt
          }
        }
        reactions { totalCount }
        comments(first: 30) {
          totalCount
          nodes {
            author { login }
            body
            createdAt
          }
          pageInfo { hasNextPage endCursor }
        }
        commits(last: 1) {
          nodes {
            commit {
              statusCheckRollup {
                state
              }
            }
          }
        }
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
}'

QUERY_PAGED='
query($cursor: String!) {
  repository(owner: "openclaw", name: "openclaw") {
    pullRequests(first: 50, states: OPEN, after: $cursor, orderBy: {field: CREATED_AT, direction: DESC}) {
      totalCount
      nodes {
        number
        title
        body
        author { login }
        createdAt
        updatedAt
        url
        isDraft
        mergeable
        additions
        deletions
        changedFiles
        labels(first: 20) { nodes { name } }
        reviewDecision
        reviews(first: 20) {
          nodes {
            author { login }
            state
            body
            createdAt
          }
        }
        reactions { totalCount }
        comments(first: 30) {
          totalCount
          nodes {
            author { login }
            body
            createdAt
          }
          pageInfo { hasNextPage endCursor }
        }
        commits(last: 1) {
          nodes {
            commit {
              statusCheckRollup {
                state
              }
            }
          }
        }
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
}'

# Classify PR size
classify_size() {
  local total=$1
  if [ "$total" -lt 10 ]; then echo "tiny"
  elif [ "$total" -lt 50 ]; then echo "small"
  elif [ "$total" -lt 200 ]; then echo "medium"
  elif [ "$total" -lt 1000 ]; then echo "large"
  else echo "huge"
  fi
}

# Map CI status
map_ci_status() {
  local state="$1"
  case "$state" in
    SUCCESS) echo "passing" ;;
    FAILURE|ERROR) echo "failing" ;;
    PENDING|EXPECTED) echo "pending" ;;
    *) echo "unknown" ;;
  esac
}

# Map mergeable
map_mergeable() {
  local val="$1"
  case "$val" in
    MERGEABLE) echo "true" ;;
    CONFLICTING) echo "false" ;;
    *) echo "unknown" ;;
  esac
}

# Map review decision
map_review_decision() {
  local val="$1"
  case "$val" in
    APPROVED) echo "approved" ;;
    CHANGES_REQUESTED) echo "changes_requested" ;;
    REVIEW_REQUIRED) echo "review_required" ;;
    null|"") echo "none" ;;
    *) echo "$val" ;;
  esac
}

# Process a single PR JSON node
process_pr() {
  local node="$1"

  local number title author created updated url body draft
  local additions deletions changed_files
  local labels_str review_decision ci_state mergeable_raw
  local comments_count reactions_total

  number=$(echo "$node" | jq -r '.number')
  title=$(echo "$node" | jq -r '.title')
  author=$(echo "$node" | jq -r '.author.login // "ghost"')
  created=$(echo "$node" | jq -r '.createdAt')
  updated=$(echo "$node" | jq -r '.updatedAt')
  url=$(echo "$node" | jq -r '.url')
  body=$(echo "$node" | jq -r '.body // ""')
  draft=$(echo "$node" | jq -r '.isDraft')

  additions=$(echo "$node" | jq -r '.additions')
  deletions=$(echo "$node" | jq -r '.deletions')
  changed_files=$(echo "$node" | jq -r '.changedFiles')
  
  local total_lines=$((additions + deletions))
  local size
  size=$(classify_size "$total_lines")

  labels_str=$(echo "$node" | jq -r '[.labels.nodes[].name] | map("\"" + . + "\"") | join(", ")')
  review_decision=$(echo "$node" | jq -r '.reviewDecision // ""')
  review_decision=$(map_review_decision "$review_decision")

  ci_state=$(echo "$node" | jq -r '.commits.nodes[0].commit.statusCheckRollup.state // ""')
  local ci_status
  ci_status=$(map_ci_status "$ci_state")

  mergeable_raw=$(echo "$node" | jq -r '.mergeable // ""')
  local mergeable_val
  mergeable_val=$(map_mergeable "$mergeable_raw")

  comments_count=$(echo "$node" | jq -r '.comments.totalCount')
  reactions_total=$(echo "$node" | jq -r '.reactions.totalCount')

  # Reviews as YAML
  local reviews_yaml
  reviews_yaml=$(echo "$node" | jq -c '[.reviews.nodes[] | {author: (.author.login // "ghost"), state: .state}]')

  # Extract "Fixes #NNN" from body
  local fixes_issues
  fixes_issues=$(echo "$body" | grep -oiE '(fix(es)?|close[sd]?|resolve[sd]?) #[0-9]+' | grep -oE '[0-9]+' | sort -un | jq -R -s 'split("\n") | map(select(length > 0) | tonumber)') 2>/dev/null || fixes_issues="[]"

  # Related PRs from body
  local related_prs
  related_prs=$(echo "$body" | grep -oE '#[0-9]+' | grep -oE '[0-9]+' | sort -un | head -20 | jq -R -s 'split("\n") | map(select(length > 0) | tonumber)') 2>/dev/null || related_prs="[]"

  # Calculate age
  local created_epoch now_epoch age_days last_activity
  created_epoch=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$created" "+%s" 2>/dev/null || date -d "$created" "+%s" 2>/dev/null || echo "0")
  now_epoch=$(date "+%s")
  age_days=$(( (now_epoch - created_epoch) / 86400 ))
  last_activity=$(echo "$updated" | cut -dT -f1)

  local outfile="$STATE_DIR/${number}.md"

  cat > "$outfile" << FRONTMATTER
---
number: $number
title: $(echo "$node" | jq '.title')
author: $author
created: $created
updated: $updated
labels: [$labels_str]
additions: $additions
deletions: $deletions
changed_files: $changed_files
size: $size
review_decision: $review_decision
reviews: $reviews_yaml
comments_count: $comments_count
reactions_total: $reactions_total
ci_status: $ci_status
mergeable: $mergeable_val
draft: $draft
url: $url
fixes_issues: $(echo "$fixes_issues" | jq -c '.')
related_prs: $(echo "$related_prs" | jq -c '.')
duplicate_of: null
---

## Description

$body

## Reviews

FRONTMATTER

  # Append reviews
  echo "$node" | jq -r '.reviews.nodes[] | "### @\(.author.login // "ghost") — \(.state) (\(.createdAt | split("T")[0]))\n\n\(.body // "")\n"' >> "$outfile" 2>/dev/null || true

  cat >> "$outfile" << EOF

## Comments

EOF

  # Append comments
  echo "$node" | jq -r '.comments.nodes[] | "### @\(.author.login // "ghost") (\(.createdAt | split("T")[0]))\n\n\(.body // "")\n"' >> "$outfile" 2>/dev/null || true

  cat >> "$outfile" << EOF

## Stats

- **Size:** $size (${additions}+, ${deletions}-, ${changed_files} files)
- **Age:** ${age_days} days
- **Last activity:** $last_activity

## Links

EOF

  # Add fixes links
  local fixes_list
  fixes_list=$(echo "$fixes_issues" | jq -r '.[] | "- Fixes: #\(.)"')
  if [ -n "$fixes_list" ]; then
    echo "$fixes_list" >> "$outfile"
  else
    echo "- Fixes: (none detected)" >> "$outfile"
  fi
}

# Main pagination loop
cursor=""
page=0
total_processed=0
total_count="?"

while true; do
  page=$((page + 1))
  log "Fetching page $page (processed so far: $total_processed / $total_count)..."

  result=""
  if [ -z "$cursor" ]; then
    result=$(cd "$REPO_DIR" && gh api graphql -f query="$QUERY_FIRST")
  else
    result=$(cd "$REPO_DIR" && gh api graphql -f query="$QUERY_PAGED" -f cursor="$cursor")
  fi

  # Check for errors
  if echo "$result" | jq -e '.errors' > /dev/null 2>&1; then
    log "GraphQL error: $(echo "$result" | jq -r '.errors[0].message')"
    log "Waiting 60s before retry..."
    sleep 60
    continue
  fi

  total_count=$(echo "$result" | jq -r '.data.repository.pullRequests.totalCount')
  nodes=$(echo "$result" | jq -c '.data.repository.pullRequests.nodes[]')

  count=0
  while IFS= read -r node; do
    [ -z "$node" ] && continue
    num=$(echo "$node" | jq -r '.number')
    log "  Processing PR #$num..."
    process_pr "$node"
    count=$((count + 1))
    total_processed=$((total_processed + 1))
  done <<< "$nodes"

  log "Page $page: processed $count PRs"

  # Check pagination
  has_next=$(echo "$result" | jq -r '.data.repository.pullRequests.pageInfo.hasNextPage')
  cursor=$(echo "$result" | jq -r '.data.repository.pullRequests.pageInfo.endCursor')

  if [ "$has_next" != "true" ]; then
    break
  fi

  sleep 1
done

log "Done! Processed $total_processed PRs total."
