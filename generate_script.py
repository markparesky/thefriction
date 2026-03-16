"""
THE FRICTION — Script Generation (Phase 2)
============================================
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
FALLBACK_MODEL = "claude-sonnet-4-20250514"  # Same model as fallback; change to claude-opus-4-6 if desired
MAX_TOKENS = 12000
TEMPERATURE = 0.70
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

    # Category validation instruction
    parts.append("--- CATEGORY CHECK ---")
    parts.append("Before writing, verify each story fits its assigned category. If a story is "
                 "entertainment, culture, science, or tech, do NOT force it into 'geopolitics' or "
                 "'domestic.' Relabel it accurately (e.g., 'culture', 'entertainment') or swap in a "
                 "story that fits. Leo should never have to hedge with 'well, sort of' when "
                 "introducing a category.")
    parts.append("")

    # Offbeat stories for Jax — with tonal tags
    offbeat = news.get("offbeat", [])
    offbeat_headlines = news.get("offbeat_headlines", [])

    parts.append("--- OFFBEAT STORIES (for Jax's Rapid Fire) ---")
    parts.append("NOTE: Stories tagged [SERIOUS] involve real human harm and should NOT be used in "
                 "Rapid Fire. Move them to the Deep Dive or Pringle segment, or cut them. "
                 "Rapid Fire should be light and funny.")
    if offbeat_headlines:
        for headline in offbeat_headlines:
            parts.append(f"- {headline}")
    elif offbeat:
        for story in offbeat:
            parts.append(f"- {story.get('title', 'No headline')} ({story.get('source', '')})")
    else:
        parts.append("- No offbeat stories available; Jax should riff on the absurdity of the main headlines instead.")
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


def parse_script_json(raw_text: str) -> dict | None:
    """
    Parse the JSON script from Claude's response.
    Handles common formatting issues (markdown fences, preamble text).
    """
    text = raw_text.strip()

    # Remove markdown code fences if present
    if text.startswith("```"):
        # Remove first line (```json or ```)
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in the text (sometimes Claude adds preamble)
    import re
    # Look for the outermost { ... } block
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # If still can't parse, log the first/last bits for debugging
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

    # Check line count (target: 120-180)
    if total_lines < 80:
        logger.warning(f"VALIDATION: Script is short ({total_lines} lines, target 120-180).")
    elif total_lines > 250:
        logger.warning(f"VALIDATION: Script is long ({total_lines} lines, target 120-180).")

    # Check character distribution
    char_counts = {}
    for line in lines:
        char = line.get("character", "UNKNOWN")
        char_counts[char] = char_counts.get(char, 0) + 1

    logger.info(f"VALIDATION: Character distribution:")
    for char in ("LEO", "PRINGLE", "BREE", "DUKE", "JAX"):
        count = char_counts.get(char, 0)
        pct = (count / total_lines * 100) if total_lines > 0 else 0
        logger.info(f"  {char}: {count} lines ({pct:.1f}%)")

    # Check Leo has roughly 50%
    leo_pct = (char_counts.get("LEO", 0) / total_lines * 100) if total_lines > 0 else 0
    if leo_pct < 35:
        logger.warning(f"VALIDATION: Leo is underrepresented ({leo_pct:.1f}%, target ~50%).")
    elif leo_pct > 65:
        logger.warning(f"VALIDATION: Leo is overrepresented ({leo_pct:.1f}%, target ~50%).")

    # Check all segments are present
    segments = set(line.get("segment", "") for line in lines)
    required_segments = {"cold_open", "brief", "deep_dive", "pringle", "rapid_fire", "daily_do"}
    missing = required_segments - segments
    if missing:
        logger.warning(f"VALIDATION: Missing segments: {missing}")

    # Check for valid characters
    valid_chars = {"LEO", "PRINGLE", "BREE", "DUKE", "JAX"}
    unknown = set(char_counts.keys()) - valid_chars
    if unknown:
        logger.warning(f"VALIDATION: Unknown characters found: {unknown}")

    # Check clips are flagged
    clips = metadata.get("clips", [])
    logger.info(f"VALIDATION: {len(clips)} clips flagged")
    if len(clips) < 2:
        logger.warning("VALIDATION: Fewer than 2 clips flagged (target 3-4).")

    # Check daily_do
    if metadata.get("daily_do"):
        logger.info(f"VALIDATION: Daily Do: \"{metadata['daily_do'][:80]}...\"")
    else:
        logger.warning("VALIDATION: No Daily Do text in metadata.")

    # Word count estimate
    total_words = sum(len(line.get("text", "").split()) for line in lines)
    est_minutes = total_words / 170  # ~170 wpm podcast pace
    logger.info(f"VALIDATION: ~{total_words} words, estimated {est_minutes:.1f} minutes")
    if est_minutes < 10:
        logger.warning(f"VALIDATION: Script may be too short ({est_minutes:.1f} min, target 15).")
    elif est_minutes > 20:
        logger.warning(f"VALIDATION: Script may be too long ({est_minutes:.1f} min, target 15).")

    # -----------------------------------------------------------------------
    # Quality checks (character, direction, tone)
    # -----------------------------------------------------------------------

    # Check direction coverage in Deep Dive and Pringle segments
    for seg_name in ("deep_dive", "pringle"):
        seg_lines = [l for l in lines if l.get("segment") == seg_name]
        if seg_lines:
            missing_dir = [l for l in seg_lines if not l.get("direction")]
            if missing_dir:
                pct_missing = len(missing_dir) / len(seg_lines) * 100
                logger.warning(
                    f"VALIDATION: {seg_name} has {len(missing_dir)}/{len(seg_lines)} "
                    f"lines ({pct_missing:.0f}%) with no direction note. Target: 0%."
                )

    # Check Leo editorializing — flag lines where Leo frames/summarizes the debate
    leo_editorial_patterns = [
        "the broader question", "the real issue", "what this comes down to",
        "what this really", "the bigger picture", "the fundamental question",
        "at the end of the day", "the bottom line here",
    ]
    deep_dive_leo = [l for l in lines
                     if l.get("segment") == "deep_dive" and l.get("character") == "LEO"]
    for l in deep_dive_leo:
        text_lower = l.get("text", "").lower()
        for pattern in leo_editorial_patterns:
            if pattern in text_lower:
                logger.warning(
                    f"VALIDATION: Leo may be editorializing (line {l.get('line', '?')}): "
                    f"contains '{pattern}'. Leo should ask questions, not frame stakes."
                )
                break

    # Check Duke argument quality — flag if Duke has no lines > 25 words in deep dive
    deep_dive_duke = [l for l in lines
                      if l.get("segment") == "deep_dive" and l.get("character") == "DUKE"]
    if deep_dive_duke:
        duke_substantive = [l for l in deep_dive_duke if len(l.get("text", "").split()) > 25]
        if not duke_substantive:
            logger.warning(
                "VALIDATION: Duke has no substantive arguments (>25 words) in the deep dive. "
                "He may be relying on one-liner dismissals instead of real arguments."
            )
        # Check for weak argument patterns
        duke_weak_patterns = [
            "at least he's being honest", "everyone has opinions", "it's not that big",
            "come on", "that's just how", "I mean, it's not",
        ]
        duke_weak_count = 0
        for l in deep_dive_duke:
            text_lower = l.get("text", "").lower()
            for pattern in duke_weak_patterns:
                if pattern in text_lower:
                    duke_weak_count += 1
                    break
        if duke_weak_count > 1:
            logger.warning(
                f"VALIDATION: Duke has {duke_weak_count} weak/dismissive arguments in the "
                f"deep dive. He needs specific facts, precedents, or reframes."
            )

    # Check Rapid Fire tonal appropriateness — flag potentially serious stories
    rapid_fire_lines = [l for l in lines if l.get("segment") == "rapid_fire"]
    serious_keywords = [
        "arrested", "jailed", "prison", "killed", "died", "death", "assault",
        "abuse", "shooting", "murdered", "sentenced", "charges dropped",
        "wrongful", "incarcerated",
    ]
    for l in rapid_fire_lines:
        text_lower = l.get("text", "").lower()
        for keyword in serious_keywords:
            if keyword in text_lower:
                logger.warning(
                    f"VALIDATION: Rapid Fire line {l.get('line', '?')} contains '{keyword}' — "
                    f"may be too serious for comedy segment. Review for tonal fit."
                )
                break

    return is_valid


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    """Execute the script generation pipeline."""
    logger.info("=" * 60)
    logger.info("THE FRICTION — Script Generation (Phase 2)")
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
    for cat in ("geopolitics", "economy", "domestic"):
        logger.info(f"Headline ({cat}): {headlines.get(cat, 'none')}")

    logger.info("\nDone.")


if __name__ == "__main__":
    run()
