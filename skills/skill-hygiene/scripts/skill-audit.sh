#!/usr/bin/env bash
# skill-audit.sh — Skill health check: overlap detection, size analysis, cleanup suggestions
# Works on macOS (bash 3) and Linux (bash 4+). No external dependencies.
#
# Usage:
#   bash skill-audit.sh                                          # Full audit
#   bash skill-audit.sh --check-duplicate "description" [name]   # Pre-install duplicate check
#
# Environment overrides:
#   SKILLS_DIR      — custom skills directory (default: ~/.claude/skills)
#   SETTINGS_FILE   — settings.json path (default: ~/.claude/settings.json)
#   AUDIT_LOG       — log file path (default: ~/.claude/skills-audit.log)

set -uo pipefail

SKILLS_DIR="${SKILLS_DIR:-$HOME/.claude/skills}"
SETTINGS_FILE="${SETTINGS_FILE:-$HOME/.claude/settings.json}"
AUDIT_LOG="${AUDIT_LOG:-$HOME/.claude/skills-audit.log}"

# Colors (disabled if not a terminal)
if [[ -t 1 ]]; then
  RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
else
  RED=''; YELLOW=''; GREEN=''; CYAN=''; BOLD=''; NC=''
fi

# English stopwords — filtered out before overlap comparison to avoid false positives
# Generic English stopwords + Claude ecosystem terms that appear in most SKILL.md descriptions
STOPWORDS="about after also and are been before being between but can could did does each for from get got had has have her here him his how into its just like may more most not now only other our out over own per she some such than that the their them then there these they this through too under use used using very was were what when where which while who will with would you your skill skills trigger triggers user users agent agents tool tools claude code prompt wants asks need needs invoke invoked help helps create creates build builds run runs"

# --- Helpers ---

extract_keywords() {
  # Extract 3+ char lowercase words, apply two-pass stemming, remove stopwords, sort unique
  local raw
  raw=$(echo "$1" | tr '[:upper:]' '[:lower:]' | grep -oE '[a-z]{3,}' | sort -u)

  local word stem
  while IFS= read -r word; do
    [[ -z "$word" ]] && continue

    # Pass 1: Strip plurals
    stem="$word"
    case "$stem" in
      *ies)  stem="${stem%ies}y" ;;
      *ves)  stem="${stem%ves}f" ;;
      *ses)  stem="${stem%ses}s" ;;  # "processes" → "process"
      *s)    stem="${stem%s}" ;;
    esac
    [[ ${#stem} -lt 3 ]] && stem="$word"

    # Pass 2: Strip derivational suffixes
    local stem2="$stem"
    case "$stem2" in
      *ation)  stem2="${stem2%ation}" ;;
      *ting)   stem2="${stem2%ting}" ;;
      *ning)   stem2="${stem2%ning}" ;;
      *ring)   stem2="${stem2%ring}" ;;
      *ling)   stem2="${stem2%ling}" ;;
      *sing)   stem2="${stem2%sing}" ;;
      *ding)   stem2="${stem2%ding}" ;;
      *ing)    stem2="${stem2%ing}" ;;
      *ment)   stem2="${stem2%ment}" ;;
      *ness)   stem2="${stem2%ness}" ;;
      *able)   stem2="${stem2%able}" ;;
      *ible)   stem2="${stem2%ible}" ;;
      *ously)  stem2="${stem2%ously}" ;;
      *ally)   stem2="${stem2%ally}" ;;
      *ely)    stem2="${stem2%ely}" ;;
      *ly)     stem2="${stem2%ly}" ;;
      *ed)     stem2="${stem2%ed}" ;;
    esac
    [[ ${#stem2} -ge 3 ]] && stem="$stem2"

    # Filter stopwords (check both original and stem)
    case " $STOPWORDS " in
      *" $stem "*) continue ;;
      *" $word "*) continue ;;
    esac

    echo "$stem"
  done <<< "$raw" | sort -u
}

get_skill_desc() {
  # Parse description from SKILL.md frontmatter, handling both single-line and multi-line YAML
  local file="$1"
  local frontmatter
  frontmatter=$(awk '/^---$/{n++; next} n==1{print} n>=2{exit}' "$file")

  # Try single-line first: description: "some text"
  local single
  single=$(echo "$frontmatter" | grep -E '^description:' | sed 's/^description:[[:space:]]*//' | tr -d '"'"'" | head -1)

  # If it's a multi-line marker (| or >) or empty, collect continuation lines
  if [[ "$single" == "|" || "$single" == ">" || -z "$single" ]]; then
    local multi
    multi=$(echo "$frontmatter" | awk '
      /^description:/ { found=1; next }
      found && /^[a-z_-]+:/ { exit }
      found && /^  / { gsub(/^  /, ""); printf "%s ", $0 }
    ')
    if [[ -n "$multi" ]]; then
      echo "$multi" | sed 's/[[:space:]]*$//'
      return
    fi
  fi

  echo "$single"
}

compute_overlap() {
  # Given two keyword files, output overlap percentage (against smaller set)
  local file_a="$1" file_b="$2"
  local common total_a total_b min_count

  common=$(comm -12 "$file_a" "$file_b" | wc -l | tr -d ' ')
  total_a=$(wc -l < "$file_a" | tr -d ' ')
  total_b=$(wc -l < "$file_b" | tr -d ' ')

  if [[ "$total_a" -le "$total_b" ]]; then min_count=$total_a; else min_count=$total_b; fi
  [[ "$min_count" -eq 0 ]] && echo "0" && return

  echo $((common * 100 / min_count))
}

# ============================================================
# Mode 1: Duplicate check
# ============================================================
if [[ "${1:-}" == "--check-duplicate" ]]; then
  NEW_DESC="${2:-}"
  NEW_NAME="${3:-unknown}"

  if [[ -z "$NEW_DESC" ]]; then
    echo "Usage: skill-audit.sh --check-duplicate \"description\" [name]"
    exit 1
  fi

  TMPDIR_CHECK=$(mktemp -d)
  trap 'rm -rf "$TMPDIR_CHECK"' EXIT

  extract_keywords "$NEW_DESC" > "$TMPDIR_CHECK/new.kw"

  echo -e "${CYAN}=== Duplicate Check: $NEW_NAME ===${NC}"
  echo ""

  FOUND=0

  for skill_dir in "$SKILLS_DIR"/*/; do
    [[ -d "$skill_dir" ]] || continue
    [[ -f "$skill_dir/SKILL.md" ]] || continue
    [[ "$(basename "$skill_dir")" == .* ]] && continue

    EXISTING_NAME=$(basename "$skill_dir")
    EXISTING_DESC=$(get_skill_desc "$skill_dir/SKILL.md")
    [[ -z "$EXISTING_DESC" ]] && continue

    extract_keywords "$EXISTING_DESC" > "$TMPDIR_CHECK/existing.kw"

    TOTAL_NEW=$(wc -l < "$TMPDIR_CHECK/new.kw" | tr -d ' ')
    [[ "$TOTAL_NEW" -eq 0 ]] && continue

    OVERLAP_COUNT=$(comm -12 "$TMPDIR_CHECK/new.kw" "$TMPDIR_CHECK/existing.kw" | wc -l | tr -d ' ')
    RATIO=$((OVERLAP_COUNT * 100 / TOTAL_NEW))

    if [[ "$RATIO" -ge 40 ]]; then
      FOUND=1
      COMMON=$(comm -12 "$TMPDIR_CHECK/new.kw" "$TMPDIR_CHECK/existing.kw" | tr '\n' ', ' | sed 's/,$//')

      if [[ "$RATIO" -ge 70 ]]; then
        echo -e "  ${RED}HIGH overlap ($RATIO%) with [$EXISTING_NAME]${NC}"
      else
        echo -e "  ${YELLOW}MEDIUM overlap ($RATIO%) with [$EXISTING_NAME]${NC}"
      fi
      echo "    Shared keywords: $COMMON"
      echo "    Existing: ${EXISTING_DESC:0:100}"
      echo ""
    fi
  done

  if [[ "$FOUND" -eq 0 ]]; then
    echo -e "  ${GREEN}No significant overlap. Safe to install.${NC}"
  else
    echo -e "  ${YELLOW}Review overlapping skills before installing.${NC}"
    echo "  Options: merge, replace, or narrow the scope of the new skill."
  fi

  exit 0
fi

# ============================================================
# Mode 2: Full audit
# ============================================================

echo -e "${BOLD}${CYAN}========================================${NC}"
echo -e "${BOLD}${CYAN}  Skill Hygiene Report — $(date '+%Y-%m-%d')${NC}"
echo -e "${BOLD}${CYAN}========================================${NC}"
echo ""

# --- Section 1: Inventory (single pass, stores metadata for later sections) ---
echo -e "${CYAN}[1] Installed Custom Skills${NC}"
echo ""

SKILL_COUNT=0
TMPDIR_AUDIT=$(mktemp -d)
trap 'rm -rf "$TMPDIR_AUDIT"' EXIT

for skill_dir in "$SKILLS_DIR"/*/; do
  [[ -d "$skill_dir" ]] || continue
  [[ -f "$skill_dir/SKILL.md" ]] || continue
  [[ "$(basename "$skill_dir")" == .* ]] && continue

  NAME=$(basename "$skill_dir")
  DESC=$(get_skill_desc "$skill_dir/SKILL.md")
  LINES=$(wc -l < "$skill_dir/SKILL.md" | tr -d ' ')
  SIZE=$(du -sh "$skill_dir" 2>/dev/null | cut -f1)
  FILE_COUNT=$(find "$skill_dir" -type f 2>/dev/null | wc -l | tr -d ' ')
  DIR_SIZE_MB=$(du -sm "$skill_dir" 2>/dev/null | cut -f1)

  # Store keywords for overlap analysis
  extract_keywords "${DESC:-}" > "$TMPDIR_AUDIT/$NAME.kw"

  # Store metadata for health check (avoids second traversal)
  echo "${FILE_COUNT}|${DIR_SIZE_MB:-0}|${DESC:+1}" > "$TMPDIR_AUDIT/$NAME.meta"

  SKILL_COUNT=$((SKILL_COUNT + 1))
  printf "  ${BOLD}%-30s${NC}  %4sL  %6s  %3s files\n" "[$NAME]" "$LINES" "$SIZE" "$FILE_COUNT"
  if [[ -n "$DESC" ]]; then
    echo "    ${DESC:0:120}"
  else
    echo -e "    ${YELLOW}(no description)${NC}"
  fi
  echo ""
done

echo "  Total: $SKILL_COUNT custom skills"
echo ""

# --- Section 2: Overlap detection ---
echo -e "${CYAN}[2] Overlap Analysis${NC}"
echo ""

NAMES=()
for f in "$TMPDIR_AUDIT"/*.kw; do
  [[ -f "$f" ]] || continue
  NAMES+=("$(basename "$f" .kw)")
done

OVERLAP_FOUND=0

# Guard against empty array under set -u
if [[ ${#NAMES[@]:-0} -gt 1 ]]; then
  for ((i=0; i<${#NAMES[@]}; i++)); do
    for ((j=i+1; j<${#NAMES[@]}; j++)); do
      A="${NAMES[$i]}"
      B="${NAMES[$j]}"

      FILE_A="$TMPDIR_AUDIT/$A.kw"
      FILE_B="$TMPDIR_AUDIT/$B.kw"

      [[ -s "$FILE_A" && -s "$FILE_B" ]] || continue

      RATIO=$(compute_overlap "$FILE_A" "$FILE_B")

      if [[ "$RATIO" -ge 40 ]]; then
        OVERLAP_FOUND=1
        SHARED=$(comm -12 "$FILE_A" "$FILE_B" | tr '\n' ', ' | sed 's/,$//')

        if [[ "$RATIO" -ge 70 ]]; then
          echo -e "  ${RED}HIGH ($RATIO%):${NC} [$A] <-> [$B]"
        else
          echo -e "  ${YELLOW}MED  ($RATIO%):${NC} [$A] <-> [$B]"
        fi
        echo "    Shared: $SHARED"
        echo ""
      fi
    done
  done
fi

if [[ "$OVERLAP_FOUND" -eq 0 ]]; then
  echo -e "  ${GREEN}No significant overlaps detected.${NC}"
fi
echo ""

# --- Section 3: Plugin count (from settings.json) ---
echo -e "${CYAN}[3] Enabled Plugins${NC}"
echo ""

PLUGIN_COUNT=0
if [[ -f "$SETTINGS_FILE" ]]; then
  PLUGINS=$(grep -oE '"[^"]+@[^"]+": true' "$SETTINGS_FILE" 2>/dev/null || true)
  if [[ -n "$PLUGINS" ]]; then
    while IFS= read -r line; do
      PLUGIN_NAME=$(echo "$line" | grep -oE '"[^"]+"' | head -1 | tr -d '"')
      echo "  - $PLUGIN_NAME"
      PLUGIN_COUNT=$((PLUGIN_COUNT + 1))
    done <<< "$PLUGINS"
  fi
  echo ""
  echo "  Enabled plugins: $PLUGIN_COUNT"
  echo "  Note: Each plugin may contribute 5-30 skills to the prompt."
else
  echo "  (settings.json not found at $SETTINGS_FILE)"
fi
echo ""

EST_PLUGIN_SKILLS=$((PLUGIN_COUNT * 8))
TOTAL=$((SKILL_COUNT + EST_PLUGIN_SKILLS))
echo -e "  Estimated total skills in prompt: ${BOLD}~${TOTAL}${NC}"
if [[ "$TOTAL" -gt 50 ]]; then
  echo -e "  ${RED}> 50 skills — high risk of selection confusion. Audit and trim.${NC}"
elif [[ "$TOTAL" -gt 30 ]]; then
  echo -e "  ${YELLOW}> 30 skills — monitor for accuracy. Consider project-level plugins.${NC}"
else
  echo -e "  ${GREEN}Healthy range.${NC}"
fi
echo ""

# --- Section 4: Health warnings (reads metadata from Section 1, no re-traversal) ---
echo -e "${CYAN}[4] Health Warnings${NC}"
echo ""

WARNINGS=0
for meta_file in "$TMPDIR_AUDIT"/*.meta; do
  [[ -f "$meta_file" ]] || continue
  NAME=$(basename "$meta_file" .meta)

  IFS='|' read -r FILE_COUNT DIR_SIZE_MB HAS_DESC < "$meta_file"

  if [[ "$FILE_COUNT" -gt 20 ]]; then
    echo -e "  ${YELLOW}[$NAME]${NC} $FILE_COUNT files — may have bundled node_modules or deps"
    WARNINGS=$((WARNINGS + 1))
  fi
  if [[ "${DIR_SIZE_MB:-0}" -gt 5 ]]; then
    echo -e "  ${YELLOW}[$NAME]${NC} ${DIR_SIZE_MB}MB — unusually large for a skill"
    WARNINGS=$((WARNINGS + 1))
  fi
  if [[ -z "$HAS_DESC" ]]; then
    echo -e "  ${YELLOW}[$NAME]${NC} missing description — hurts tool selection"
    WARNINGS=$((WARNINGS + 1))
  fi
done

if [[ "$WARNINGS" -eq 0 ]]; then
  echo -e "  ${GREEN}All skills healthy.${NC}"
fi
echo ""

# --- Section 5: Archived skills ---
ARCHIVED=()
for d in "$SKILLS_DIR"/.archived-*/; do
  [[ -d "$d" ]] || continue
  ARCHIVED+=("$(basename "$d" | sed 's/^\.archived-//')")
done

if [[ ${#ARCHIVED[@]:-0} -gt 0 ]]; then
  echo -e "${CYAN}[5] Archived Skills${NC}"
  echo ""
  for a in "${ARCHIVED[@]}"; do
    echo "  - $a (restore: mv ~/.claude/skills/.archived-$a ~/.claude/skills/$a)"
  done
  echo ""
fi

# --- Append audit log ---
{
  echo "--- $(date '+%Y-%m-%d %H:%M') | custom=$SKILL_COUNT plugins=$PLUGIN_COUNT est_total=$TOTAL overlaps=$OVERLAP_FOUND warnings=$WARNINGS ---"
} >> "$AUDIT_LOG" 2>/dev/null || true

echo -e "${GREEN}Done. Log: $AUDIT_LOG${NC}"
