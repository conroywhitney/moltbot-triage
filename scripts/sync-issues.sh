#!/usr/bin/env bash
# sync-issues.sh — Pull all open issues from moltbot/moltbot with body + comments
# Must run gh from the fork directory for auth context
set -euo pipefail

REPO_DIR="/Users/conroywhitney/clawdbot"
STATE_DIR="$(cd "$(dirname "$0")/.." && pwd)/state/issues"
OWNER="moltbot"
REPO="moltbot"
PAGE_SIZE=100
COMMENT_LIMIT=50  # max comments per issue in GraphQL (paginate if more)

mkdir -p "$STATE_DIR"

log() { echo "[sync-issues] $*" >&2; }

# GraphQL query template
read -r -d '' QUERY_TEMPLATE << 'GRAPHQL' || true
query($cursor: String) {
  repository(owner: "OWNER_PLACEHOLDER", name: "REPO_PLACEHOLDER") {
    issues(first: PAGE_SIZE_PLACEHOLDER, states: OPEN, after: $cursor, orderBy: {field: CREATED_AT, direction: DESC}) {
      totalCount
      nodes {
        number
        title
        body
        author { login }
        createdAt
        updatedAt
        url
        labels(first: 20) { nodes { name } }
        assignees(first: 10) { nodes { login } }
        reactions { totalCount }
        comments(first: COMMENT_LIMIT_PLACEHOLDER) {
          totalCount
          nodes {
            author { login }
            body
            createdAt
          }
          pageInfo { hasNextPage endCursor }
        }
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
}
GRAPHQL

# Substitute placeholders
QUERY_TEMPLATE="${QUERY_TEMPLATE//OWNER_PLACEHOLDER/$OWNER}"
QUERY_TEMPLATE="${QUERY_TEMPLATE//REPO_PLACEHOLDER/$REPO}"
QUERY_TEMPLATE="${QUERY_TEMPLATE//PAGE_SIZE_PLACEHOLDER/$PAGE_SIZE}"
QUERY_TEMPLATE="${QUERY_TEMPLATE//COMMENT_LIMIT_PLACEHOLDER/$COMMENT_LIMIT}"

# Fetch additional comments for an issue if needed
fetch_remaining_comments() {
  local issue_number="$1"
  local after_cursor="$2"
  local all_comments=""
  local cursor="$after_cursor"

  while true; do
    local result
    result=$(cd "$REPO_DIR" && gh api graphql -f query="
      query {
        repository(owner: \"$OWNER\", name: \"$REPO\") {
          issue(number: $issue_number) {
            comments(first: 100, after: \"$cursor\") {
              nodes {
                author { login }
                body
                createdAt
              }
              pageInfo { hasNextPage endCursor }
            }
          }
        }
      }
    ")
    
    local has_next
    has_next=$(echo "$result" | jq -r '.data.repository.issue.comments.pageInfo.hasNextPage')
    cursor=$(echo "$result" | jq -r '.data.repository.issue.comments.pageInfo.endCursor')
    
    if [ -z "$all_comments" ]; then
      all_comments=$(echo "$result" | jq '.data.repository.issue.comments.nodes')
    else
      all_comments=$(echo "$all_comments" "$result" | jq -s '.[0] + (.[1] | .data.repository.issue.comments.nodes)')
    fi

    if [ "$has_next" != "true" ]; then
      break
    fi
    sleep 0.5
  done

  echo "$all_comments"
}

# Process a single issue JSON node into a markdown file
process_issue() {
  local node="$1"
  local number title author created updated url body
  local labels_json assignees_json comments_count reactions_total
  local comments_has_next comments_end_cursor

  number=$(echo "$node" | jq -r '.number')
  title=$(echo "$node" | jq -r '.title' | sed 's/"/\\"/g')
  author=$(echo "$node" | jq -r '.author.login // "ghost"')
  created=$(echo "$node" | jq -r '.createdAt')
  updated=$(echo "$node" | jq -r '.updatedAt')
  url=$(echo "$node" | jq -r '.url')
  body=$(echo "$node" | jq -r '.body // ""')
  
  # Labels as YAML array
  labels_json=$(echo "$node" | jq -r '[.labels.nodes[].name] | map("\"" + . + "\"") | join(", ")')
  assignees_json=$(echo "$node" | jq -r '[.assignees.nodes[].login] | join(", ")')
  comments_count=$(echo "$node" | jq -r '.comments.totalCount')
  reactions_total=$(echo "$node" | jq -r '.reactions.totalCount')

  # Get initial comments
  local comments
  comments=$(echo "$node" | jq '.comments.nodes')
  comments_has_next=$(echo "$node" | jq -r '.comments.pageInfo.hasNextPage')
  comments_end_cursor=$(echo "$node" | jq -r '.comments.pageInfo.endCursor')

  # Fetch remaining comments if paginated
  if [ "$comments_has_next" = "true" ]; then
    log "  Issue #$number has more than $COMMENT_LIMIT comments, fetching remaining..."
    local extra_comments
    extra_comments=$(fetch_remaining_comments "$number" "$comments_end_cursor")
    comments=$(echo "$comments" "$extra_comments" | jq -s '.[0] + .[1]')
  fi

  # Extract linking info from body
  local duplicate_of=""
  if echo "$body" | grep -qiE '(duplicate of|duplicates?) #[0-9]+'; then
    duplicate_of=$(echo "$body" | grep -oiE '(duplicate of|duplicates?) #[0-9]+' | head -1 | grep -oE '[0-9]+')
  fi

  local related_issues
  related_issues=$(echo "$body" | grep -oE '#[0-9]+' | grep -oE '[0-9]+' | sort -un | head -20 | jq -R -s 'split("\n") | map(select(length > 0) | tonumber)') 2>/dev/null || related_issues="[]"

  # Build the markdown file
  local outfile="$STATE_DIR/${number}.md"
  
  cat > "$outfile" << FRONTMATTER
---
number: $number
title: "$(echo "$title" | sed 's/"/\\"/g')"
author: $author
created: $created
updated: $updated
labels: [$labels_json]
assignees: [${assignees_json}]
comments_count: $comments_count
reactions_total: $reactions_total
url: $url
duplicate_of: ${duplicate_of:-null}
related_issues: $(echo "$related_issues" | jq -c '.')
blocks: []
blocked_by: []
---

## Description

$body

## Comments

FRONTMATTER

  # Append comments
  echo "$comments" | jq -r '.[] | "### @\(.author.login // "ghost") (\(.createdAt | split("T")[0]))\n\n\(.body // "")\n"' >> "$outfile" 2>/dev/null || true

  cat >> "$outfile" << 'EOF'

## Links

- None detected yet
EOF
}

# Build first-page query (no cursor variable)
QUERY_FIRST="${QUERY_TEMPLATE//after: \$cursor/after: null}"
QUERY_FIRST="${QUERY_FIRST//query(\$cursor: String)/query}"

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
    result=$(cd "$REPO_DIR" && gh api graphql -f query="$QUERY_TEMPLATE" -f cursor="$cursor")
  fi

  # Check for errors
  if echo "$result" | jq -e '.errors' > /dev/null 2>&1; then
    log "GraphQL error: $(echo "$result" | jq -r '.errors[0].message')"
    log "Waiting 60s before retry..."
    sleep 60
    continue
  fi

  total_count=$(echo "$result" | jq -r '.data.repository.issues.totalCount')
  nodes=$(echo "$result" | jq -c '.data.repository.issues.nodes[]')
  
  count=0
  while IFS= read -r node; do
    [ -z "$node" ] && continue
    num=$(echo "$node" | jq -r '.number')
    log "  Processing issue #$num..."
    process_issue "$node"
    count=$((count + 1))
    total_processed=$((total_processed + 1))
  done <<< "$nodes"

  log "Page $page: processed $count issues"

  # Check pagination
  has_next=$(echo "$result" | jq -r '.data.repository.issues.pageInfo.hasNextPage')
  cursor=$(echo "$result" | jq -r '.data.repository.issues.pageInfo.endCursor')

  if [ "$has_next" != "true" ]; then
    break
  fi

  # Rate limit: small pause between pages
  sleep 1
done

log "Done! Processed $total_processed issues total."
