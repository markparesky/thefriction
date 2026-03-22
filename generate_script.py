"""
THE FRICTION — Script Generation (Phase 2) v3
===============================================
Two-part structure: The Download + The Hangout
Takes daily_news.json from Phase 1 and calls the Claude API
to generate a complete episode script in JSON format.

Output: daily_script.json

Requirements:
  - ANTHROPIC_API_KEY environment variable set
  - daily_news.json from Phase 1
  - system_prompt.txt in the same directory
"""

import os
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("friction.scriptgen")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
NEWS_INPUT_FILE = os.getenv("NEWS_OUTPUT_FILE", "daily_news.json")
SCRIPT_OUTPUT_FILE = os.getenv("SCRIPT_OUTPUT_FILE", "daily_script.json")
SYSTEM_PROMPT_FILE = "system_prompt.txt"

# Model settings
PRIMARY_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
FALLBACK_MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 16000  # Increased for longer two-part scripts
TEMPERATURE = 0.75  # Balanced: structured enough for JSON, loose enough for comedy
MAX_RETRIES = 2


# ---------------------------------------------------------------------------
# Load Inputs
# ---------------------------------------------------------------------------

def load_news() -> dict:
    """Load the daily news JSON from Phase 1."""
    path = Path(NEWS_INPUT_FILE)
    if not path.exists():
        logger.error(f"News file not found: {NEWS_INPUT_FILE}")
        logger.error("Make sure Phase 1 (news_ingestion.py) ran successfully first.")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Verify we have stories
    stories = data.get("stories", {})
    story_count = sum(1 for k in ("geopolitics", "economy", "domestic") if stories.get(k))
    if story_count == 0:
        logger.error("News file contains no stories. Phase 1 may have failed.")
        sys.exit(1)

    logger.info(f"Loaded news for {data.get('episode_date', 'unknown date')}")
    logger.info(f"  Stories: {story_count} main + {len(data.get('offbeat', []))} offbeat")
    if data.get("episode_archetype"):
        logger.info(f"  Suggested archetype: {data['episode_archetype']}")

    return data


def load_system_prompt() -> str:
    """Load the system prompt from file."""
    path = Path(SYSTEM_PROMPT_FILE)
    if not path.exists():
        logger.error(f"System prompt not found: {SYSTEM_PROMPT_FILE}")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        prompt = f.read().strip()

    logger.info(f"Loaded system prompt ({len(prompt.split())} words)")
    return prompt


# ---------------------------------------------------------------------------
# Build the User Message
# ---------------------------------------------------------------------------

def build_user_message(news: dict) -> str:
    """
    Build the user message that contains the day's news stories.
    This is what gets sent alongside the system prompt.
    """
    parts = []

    parts.append(f"Today's date: {news.get('episode_date', datetime.now().strftime('%Y-%m-%d'))}")
    parts.append("")

    # Suggested archetype from pre-processing (if available)
    if news.get("episode_archetype"):
        parts.append(f"SUGGESTED EPISODE ARCHETYPE: {news['episode_archetype']}")
        if news.get("archetype_reasoning"):
            parts.append(f"Reasoning: {news['archetype_reasoning']}")
        parts.append("")

    # Main stories
    stories = news.get("stories", {})
    preprocessed = news.get("preprocessed", {})

    for category in ("geopolitics", "economy", "domestic"):
        story = stories.get(category)
        if not story:
            continue

        parts.append(f"--- {category.upper()} ---")
        parts.append(f"Headline: {story.get('title', 'No headline')}")
        parts.append(f"Source: {story.get('source', 'Unknown')}")

        # Use preprocessed summary if available, otherwise full text
        pre = preprocessed.get(category, {})
        if pre.get("summary"):
            parts.append(f"Summary: {pre['summary']}")
        if pre.get("key_entities"):
            parts.append(f"Key entities: {', '.join(pre['key_entities'])}")
        if pre.get("debate_angles"):
            parts.append(f"Debate angles: {'; '.join(pre['debate_angles'])}")
        if pre.get("pringle_suggestion"):
            parts.append(f"Pringle suggestion: {pre['pringle_suggestion']}")

        # Full article text (truncated)
        full_text = story.get("full_text", story.get("description", ""))
        if full_text:
            parts.append(f"Full text: {full_text[:3000]}")

        parts.append("")

    # Offbeat stories for Jax's section in The Hangout
    offbeat = news.get("offbeat", [])
    offbeat_headlines = news.get("offbeat_headlines", [])

    parts.append("--- OFFBEAT STORIES (for Jax's Offbeat section in The Hangout) ---")
    parts.append("These should be LIGHT and FUNNY only. No stories involving serious human harm.")
    if offbeat_headlines:
        for headline in offbeat_headlines:
            parts.append(f"- {headline}")
    elif offbeat:
        for story in offbeat:
            parts.append(f"- {story.get('title', 'No headline')} ({story.get('source', '')})")
    else:
        parts.append("- No offbeat stories available; Jax should riff on the absurdity "
                     "of the main headlines instead.")
    parts.append("")

    # Reminders for the two-part structure
    parts.append("--- STRUCTURE REMINDERS ---")
    parts.append("PART 1 (The Download): Leo + Pringle ONLY. No Jax. No Duke "
                 "(except maybe one brief reaction). Serious, authoritative, no forced comedy.")
    parts.append("PART 2 (The Hangout): ALL FOUR characters. Loose, funny, personal. "
                 "The Confessional section needs at least one genuinely embarrassing, "
                 "specific personal story from Jax (4-6 lines minimum).")
    parts.append("EVERY line must have a non-null direction field.")
    parts.append("Target: 150-200 lines total, with The Hangout roughly TWICE as long "
                 "as The Download.")
    parts.append("")

    parts.append("Write today's complete episode script now.")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Call Claude API
# ---------------------------------------------------------------------------

def generate_script(system_prompt: str, user_message: str) -> dict:
    """
    Call the Anthropic API to generate the episode script.
    Uses raw HTTP requests instead of the SDK to avoid compatibility issues.
    Returns the parsed JSON script.
    """
    import requests as req

    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY not set. Add it to your environment variables.")
        sys.exit(1)

    logger.info(f"API key starts with: {ANTHROPIC_API_KEY[:12]}...")

    models_to_try = [PRIMARY_MODEL, FALLBACK_MODEL]

    for model in models_to_try:
        for attempt in range(1, MAX_RETRIES + 1):
            logger.info(f"Generating script (model: {model}, attempt {attempt}/{MAX_RETRIES})...")
            logger.info(f"  Temperature: {TEMPERATURE}")
            logger.info(f"  Max tokens: {MAX_TOKENS}")
            logger.info(f"  Using raw HTTP request (no SDK)...")

            try:
                logger.info("  Sending API request...")
                response = req.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": model,
                        "max_tokens": MAX_TOKENS,
                        "temperature": TEMPERATURE,
                        "system": system_prompt,
                        "messages": [{"role": "user", "content": user_message}],
                    },
                    timeout=300,
                )

                logger.info(f"  Response status: {response.status_code}")

                if response.status_code != 200:
                    error_text = response.text[:500]
                    logger.error(f"  API error: {error_text}")
                    if attempt < MAX_RETRIES:
                        logger.info("  Retrying in 10 seconds...")
                        import time
                        time.sleep(10)
                    continue

                data = response.json()

                # Extract text from response
                raw_text = ""
                for block in data.get("content", []):
                    if block.get("type") == "text":
                        raw_text += block.get("text", "")

                usage = data.get("usage", {})
                logger.info(f"  Response length: {len(raw_text)} characters")
                logger.info(f"  Input tokens: {usage.get('input_tokens', '?')}")
                logger.info(f"  Output tokens: {usage.get('output_tokens', '?')}")
                logger.info(f"  Stop reason: {data.get('stop_reason', '?')}")

                # Parse JSON from response
                script = parse_script_json(raw_text)

                if script:
                    logger.info("  Script parsed successfully!")
                    script = fix_encoding(script)
                    return script
                else:
                    logger.warning(f"  Failed to parse JSON from response.")
                    logger.warning(f"  First 500 chars: {raw_text[:500]}")

            except req.exceptions.Timeout:
                logger.error(f"  Request timed out after 300 seconds.")
            except Exception as e:
                logger.error(f"  API call failed: {type(e).__name__}: {e}")
                import traceback
                logger.error(f"  Traceback: {traceback.format_exc()}")

            if attempt < MAX_RETRIES:
                logger.info("  Retrying in 10 seconds...")
                import time
                time.sleep(10)

        logger.warning(f"All attempts with model {model} failed. Trying next model...")

    logger.error("All attempts with all models failed.")
    sys.exit(1)


def fix_encoding(script: dict) -> dict:
    """
    Fix common UTF-8 encoding artifacts in script text fields.
    These appear as mojibake when em dashes, curly quotes, etc.
    get double-encoded or mangled in transit.
    """
    replacements = {
        "â€"": "—",   # em dash
        "â€"": "–",   # en dash
        "â€˜": "'",   # left single quote
        "â€™": "'",   # right single quote / apostrophe
        "â€œ": '"',   # left double quote
        "â€\x9d": '"',  # right double quote
        "â€¦": "…",   # ellipsis
        "\u00e2\u0080\u0094": "—",
        "\u00e2\u0080\u0093": "–",
    }

    lines = script.get("script", [])
    fixed_count = 0
    for line in lines:
        text = line.get("text", "")
        original = text
        for bad, good in replacements.items():
            text = text.replace(bad, good)
        if text != original:
            line["text"] = text
            fixed_count += 1

        # Also fix direction field
        direction = line.get("direction", "")
        if direction:
            for bad, good in replacements.items():
                direction = direction.replace(bad, good)
            line["direction"] = direction

    if fixed_count > 0:
        logger.info(f"  Fixed encoding artifacts in {fixed_count} lines.")

    return script


def parse_script_json(raw_text: str) -> dict | None:
    """
    Parse the JSON script from Claude's response.
    Handles common formatting issues (markdown fences, preamble text).
    """
    text = raw_text.strip()

    # Remove markdown code fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in the text
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    logger.error(f"Could not parse JSON. First 200 chars: {text[:200]}")
    logger.error(f"Last 200 chars: {text[-200:]}")
    return None


# ---------------------------------------------------------------------------
# Validate Script
# ---------------------------------------------------------------------------

def validate_script(script: dict) -> bool:
    """
    Validate the generated script for structure and quality.
    Returns True if valid, logs warnings for issues.
    """
    is_valid = True

    # Check metadata exists
    metadata = script.get("metadata", {})
    if not metadata:
        logger.warning("VALIDATION: No metadata block found.")
        is_valid = False

    # Check script array exists
    lines = script.get("script", [])
    if not lines:
        logger.warning("VALIDATION: No script lines found.")
        return False

    total_lines = len(lines)
    logger.info(f"VALIDATION: {total_lines} total lines")

    # Check line count (target: 150-200)
    if total_lines < 120:
        logger.warning(f"VALIDATION: Script is short ({total_lines} lines, target 150-200). "
                       f"The Hangout likely needs more content.")
    elif total_lines > 250:
        logger.warning(f"VALIDATION: Script is long ({total_lines} lines, target 150-200).")

    # Check character distribution
    char_counts = {}
    for line in lines:
        char = line.get("character", "UNKNOWN")
        char_counts[char] = char_counts.get(char, 0) + 1

    logger.info(f"VALIDATION: Character distribution:")
    for char in ("LEO", "PRINGLE", "DUKE", "JAX"):
        count = char_counts.get(char, 0)
        pct = (count / total_lines * 100) if total_lines > 0 else 0
        logger.info(f"  {char}: {count} lines ({pct:.1f}%)")

    # Check for BREE (should not be present in v3)
    if char_counts.get("BREE", 0) > 0:
        logger.warning(f"VALIDATION: BREE appears in script ({char_counts['BREE']} lines). "
                       f"Bree was removed in v3 — only LEO, PRINGLE, DUKE, JAX.")
        is_valid = False

    # Check valid characters
    valid_chars = {"LEO", "PRINGLE", "DUKE", "JAX"}
    unknown = set(char_counts.keys()) - valid_chars
    if unknown:
        logger.warning(f"VALIDATION: Unknown characters found: {unknown}")

    # -----------------------------------------------------------------------
    # Two-part structure checks
    # -----------------------------------------------------------------------

    # Segment distribution
    segments = {}
    for line in lines:
        seg = line.get("segment", "unknown")
        segments[seg] = segments.get(seg, 0) + 1

    logger.info(f"VALIDATION: Segment distribution:")
    for seg, count in sorted(segments.items()):
        logger.info(f"  {seg}: {count} lines")

    # Check Download vs Hangout balance
    download_segments = {"cold_open", "download_headlines", "download_pringle", "bridge"}
    hangout_segments = {"hangout_reaction", "hangout_confessional", "hangout_offbeat",
                       "hangout_daily_do"}

    download_lines = sum(segments.get(s, 0) for s in download_segments)
    hangout_lines = sum(segments.get(s, 0) for s in hangout_segments)

    logger.info(f"VALIDATION: Download: {download_lines} lines, Hangout: {hangout_lines} lines")
    if hangout_lines > 0 and download_lines > 0:
        ratio = hangout_lines / download_lines
        logger.info(f"VALIDATION: Hangout/Download ratio: {ratio:.1f}x (target: ~2x)")
        if ratio < 1.5:
            logger.warning("VALIDATION: Hangout is too short relative to Download. "
                          "Target is Hangout ~2x longer.")

    # Check Jax is NOT in The Download
    download_segment_names = download_segments
    for line in lines:
        if line.get("segment") in download_segment_names and line.get("character") == "JAX":
            logger.warning(f"VALIDATION: Jax appears in Download segment '{line['segment']}' "
                          f"(line {line.get('line', '?')}). Jax should only be in The Hangout.")
            is_valid = False
            break

    # Check Duke is minimal in The Download
    duke_download = sum(1 for l in lines
                       if l.get("segment") in download_segment_names
                       and l.get("character") == "DUKE")
    if duke_download > 2:
        logger.warning(f"VALIDATION: Duke has {duke_download} lines in The Download "
                      f"(max 1-2 allowed).")

    # Check confessional section exists and has substance
    confessional_lines = [l for l in lines if l.get("segment") == "hangout_confessional"]
    logger.info(f"VALIDATION: Confessional section: {len(confessional_lines)} lines")
    if len(confessional_lines) < 15:
        logger.warning(f"VALIDATION: Confessional section is thin ({len(confessional_lines)} "
                      f"lines, target 20-30). This is the heart of the show.")

    # Check for Jax extended story in confessional
    jax_confessional = [l for l in confessional_lines if l.get("character") == "JAX"]
    jax_long_lines = [l for l in jax_confessional if len(l.get("text", "").split()) > 30]
    if not jax_long_lines:
        logger.warning("VALIDATION: Jax has no extended confessional story (>30 words). "
                      "He needs at least one genuinely embarrassing personal story.")

    # -----------------------------------------------------------------------
    # Direction coverage
    # -----------------------------------------------------------------------
    missing_direction = [l for l in lines if not l.get("direction")]
    if missing_direction:
        pct = len(missing_direction) / total_lines * 100
        logger.warning(f"VALIDATION: {len(missing_direction)}/{total_lines} lines "
                      f"({pct:.0f}%) have no direction note. Target: 0%.")

    # -----------------------------------------------------------------------
    # Imperfection checks (Hangout only)
    # -----------------------------------------------------------------------
    hangout_text = " ".join(l.get("text", "") for l in lines
                           if l.get("segment", "").startswith("hangout"))

    # Check for false starts (em dash mid-sentence)
    false_starts = len(re.findall(r'\w+\s*[—–-]\s*', hangout_text))
    logger.info(f"VALIDATION: ~{false_starts} false starts detected in Hangout "
               f"(target: 5-8)")
    if false_starts < 3:
        logger.warning("VALIDATION: Too few false starts in Hangout. Dialogue may sound "
                      "too polished.")

    # Check for verbal fillers
    filler_words = ["um,", "uh,", "like,", "i mean,", "you know,", "look,",
                    "here's the thing", "honestly,"]
    filler_count = sum(hangout_text.lower().count(f) for f in filler_words)
    logger.info(f"VALIDATION: ~{filler_count} verbal fillers in Hangout")
    if filler_count < 5:
        logger.warning("VALIDATION: Too few verbal fillers. Hangout dialogue may sound "
                      "too clean.")

    # -----------------------------------------------------------------------
    # Word count and timing
    # -----------------------------------------------------------------------
    total_words = sum(len(line.get("text", "").split()) for line in lines)
    est_minutes = total_words / 170
    logger.info(f"VALIDATION: ~{total_words} words, estimated {est_minutes:.1f} minutes")
    if est_minutes < 10:
        logger.warning(f"VALIDATION: Script may be too short ({est_minutes:.1f} min, target 15).")
    elif est_minutes > 20:
        logger.warning(f"VALIDATION: Script may be too long ({est_minutes:.1f} min, target 15).")

    # Check clips
    clips = metadata.get("clips", [])
    logger.info(f"VALIDATION: {len(clips)} clips flagged")
    if len(clips) < 2:
        logger.warning("VALIDATION: Fewer than 2 clips flagged (target 3-4).")

    # Check daily_do
    if metadata.get("daily_do"):
        logger.info(f"VALIDATION: Daily Do: \"{metadata['daily_do'][:80]}...\"")
    else:
        logger.warning("VALIDATION: No Daily Do text in metadata.")

    # Personal life references
    personal_keywords = [
        "jenny", "mia", "peggy", "my daughter", "my kid", "my wife",
        "my mom", "my dad", "my mother", "my father", "my granddaughter",
        "the twins", "little league", "the situation",  # Jax's cat
        "my ex", "my barber", "my landlord", "my neighbor",
    ]
    hangout_lower = hangout_text.lower()
    personal_refs = sum(1 for kw in personal_keywords if kw in hangout_lower)
    logger.info(f"VALIDATION: ~{personal_refs} personal life references in Hangout "
               f"(target: 8-10)")
    if personal_refs < 4:
        logger.warning("VALIDATION: Too few personal life references. Characters don't "
                      "feel like real people.")

    return is_valid


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    """Execute the script generation pipeline."""
    logger.info("=" * 60)
    logger.info("THE FRICTION — Script Generation (Phase 2) v3")
    logger.info(f"Run time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 60)

    # Load inputs
    logger.info("\nLoading news data...")
    news = load_news()

    logger.info("\nLoading system prompt...")
    system_prompt = load_system_prompt()

    # Build user message
    logger.info("\nBuilding user message...")
    user_message = build_user_message(news)
    logger.info(f"User message: {len(user_message.split())} words")

    # Generate script
    logger.info("\nCalling Claude API...")
    script = generate_script(system_prompt, user_message)

    # Validate
    logger.info("\nValidating script...")
    validate_script(script)

    # Save output
    with open(SCRIPT_OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(script, f, indent=2, ensure_ascii=False)

    logger.info(f"\nScript saved to: {SCRIPT_OUTPUT_FILE}")

    # Log summary
    metadata = script.get("metadata", {})
    logger.info(f"Episode date: {metadata.get('episode_date', 'unknown')}")
    logger.info(f"Episode archetype: {metadata.get('episode_archetype', 'unknown')}")
    logger.info(f"Pringle mode: {metadata.get('pringle_mode', 'unknown')}")
    headlines = metadata.get("headlines", {})
    for key, val in headlines.items():
        logger.info(f"Headline ({key}): {val}")

    logger.info("\nDone.")


if __name__ == "__main__":
    run()
