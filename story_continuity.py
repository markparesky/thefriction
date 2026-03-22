"""
THE FRICTION — Story Continuity Tracker
=========================================
Tracks running personal storylines across episodes.
These are the recurring bits, relationships, and sagas
that give the show continuity and make characters feel real.

After each episode, this script extracts storyline updates
and saves them to GitHub. Before each episode, the generate
script loads these storylines and injects them into the
user message so the model can advance them.

Requirements:
  - GITHUB_TOKEN environment variable
"""

import os
import json
import logging
import requests
import base64
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("friction.continuity")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = "markparesky/thefriction"
CONTINUITY_FILE_PATH = "data/story_continuity.json"

SCRIPT_INPUT_FILE = os.getenv("SCRIPT_OUTPUT_FILE", "daily_script.json")

# The Anthropic API key for the extraction call
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
EXTRACTION_MODEL = "claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# GitHub read/write
# ---------------------------------------------------------------------------

def load_continuity_from_github() -> dict:
    """Load the story continuity JSON from GitHub."""
    if not GITHUB_TOKEN:
        logger.warning("GITHUB_TOKEN not set. Cannot load continuity.")
        return {"storylines": [], "last_updated": ""}

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{CONTINUITY_FILE_PATH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)

        if response.status_code == 200:
            content_b64 = response.json().get("content", "")
            content = base64.b64decode(content_b64).decode("utf-8")
            continuity = json.loads(content)
            logger.info(f"Loaded continuity: {len(continuity.get('storylines', []))} "
                       f"active storylines")
            return continuity

        elif response.status_code == 404:
            logger.info("No continuity file found (first run). Starting fresh.")
            return {"storylines": [], "last_updated": ""}

        else:
            logger.warning(f"Could not load continuity: {response.status_code}")
            return {"storylines": [], "last_updated": ""}

    except Exception as e:
        logger.warning(f"Error loading continuity: {e}")
        return {"storylines": [], "last_updated": ""}


def save_continuity_to_github(continuity: dict):
    """Save the story continuity JSON to GitHub."""
    if not GITHUB_TOKEN:
        logger.warning("GITHUB_TOKEN not set. Cannot save continuity.")
        return

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{CONTINUITY_FILE_PATH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    content_json = json.dumps(continuity, indent=2, ensure_ascii=False)
    content_b64 = base64.b64encode(content_json.encode("utf-8")).decode("utf-8")

    # Check if file exists to get SHA
    sha = None
    try:
        check = requests.get(url, headers=headers, timeout=30)
        if check.status_code == 200:
            sha = check.json().get("sha")
    except Exception:
        pass

    payload = {
        "message": f"Story continuity update: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        "content": content_b64,
    }
    if sha:
        payload["sha"] = sha

    try:
        response = requests.put(url, headers=headers, json=payload, timeout=30)
        if response.status_code in (200, 201):
            logger.info("Continuity saved to GitHub successfully.")
        else:
            logger.error(f"Failed to save continuity: {response.status_code} "
                        f"— {response.text[:200]}")
    except Exception as e:
        logger.error(f"Error saving continuity: {e}")


# ---------------------------------------------------------------------------
# Storyline extraction (uses Claude to read the script and update storylines)
# ---------------------------------------------------------------------------

def extract_storylines_from_script(script: dict, existing: dict) -> dict:
    """
    Use Claude to read today's script and extract/update running storylines.
    Returns updated continuity dict.
    """
    if not ANTHROPIC_API_KEY:
        logger.warning("No API key. Cannot extract storylines.")
        return existing

    # Build the extraction prompt
    existing_json = json.dumps(existing.get("storylines", []), indent=2)
    script_json = json.dumps(script.get("script", []), indent=2)

    system_prompt = """You maintain a running list of personal storylines from a daily podcast called The Friction. These are recurring bits, relationships, and sagas that develop across episodes.

Your job: Read today's script, identify any personal storylines mentioned, and update the storyline list.

For EXISTING storylines: Update them with what happened today. Add the new development to the timeline.
For NEW storylines: Add them to the list with today as the start date.

Each storyline should have:
- character: Who it belongs to (JAX, DUKE, LEO, PRINGLE)
- title: Short name (e.g., "Jax's landlord girlfriend", "Duke's Inside Out crying photo")
- status: "active" (still developing), "dormant" (hasn't been mentioned in a while), or "resolved" (story concluded)
- summary: Current state of the storyline in 1-2 sentences
- timeline: Array of {date, event} entries showing how it's developed
- next_beats: 2-3 suggestions for where this storyline could go next

IMPORTANT: Not every episode mention is a storyline. Only track things that have RECURRING potential — relationships, ongoing situations, running bits. A one-off joke is not a storyline.

Return ONLY valid JSON: {"storylines": [...]}"""

    user_message = f"""EXISTING STORYLINES:
{existing_json}

TODAY'S SCRIPT:
{script_json}

Update the storylines based on today's script. Return the complete updated storylines list as JSON."""

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": EXTRACTION_MODEL,
                "max_tokens": 4000,
                "temperature": 0.3,  # Low temp for accurate extraction
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_message}],
            },
            timeout=120,
        )

        if response.status_code != 200:
            logger.error(f"Extraction API error: {response.text[:300]}")
            return existing

        data = response.json()
        raw_text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                raw_text += block.get("text", "")

        # Parse JSON
        text = raw_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        import re
        # Try direct parse
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                result = json.loads(match.group())
            else:
                logger.error("Could not parse storyline extraction.")
                return existing

        updated = {
            "storylines": result.get("storylines", []),
            "last_updated": datetime.now(timezone.utc).strftime('%Y-%m-%d'),
        }

        logger.info(f"Extracted {len(updated['storylines'])} storylines from today's script.")
        return updated

    except Exception as e:
        logger.error(f"Storyline extraction failed: {e}")
        return existing


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def load_continuity() -> dict:
    """Load continuity data. Called by generate_script.py before generation."""
    return load_continuity_from_github()


def format_continuity_for_prompt(continuity: dict) -> str:
    """
    Format the continuity data as text to inject into the user message.
    This tells the model what storylines to continue.
    """
    storylines = continuity.get("storylines", [])
    if not storylines:
        return ""

    active = [s for s in storylines if s.get("status") == "active"]
    if not active:
        return ""

    lines = [
        "--- RUNNING STORYLINES (advance these in The Hangout) ---",
        "These are ongoing personal storylines from previous episodes.",
        "ADVANCE at least 2-3 of these today. Add new developments,",
        "don't just repeat what happened before. The audience knows",
        "the backstory — give them the NEXT chapter.",
        "",
    ]

    for s in active:
        lines.append(f"[{s.get('character', '?')}] {s.get('title', 'Untitled')}")
        lines.append(f"  Status: {s.get('summary', 'No summary')}")

        timeline = s.get("timeline", [])
        if timeline:
            recent = timeline[-3:]  # Last 3 events
            for entry in recent:
                lines.append(f"  - {entry.get('date', '?')}: {entry.get('event', '?')}")

        next_beats = s.get("next_beats", [])
        if next_beats:
            lines.append(f"  Possible next beats: {'; '.join(next_beats)}")

        lines.append("")

    lines.append("You can also introduce NEW storylines if the day's news triggers one.")
    lines.append("But make sure to advance the existing ones — listeners are following these.")
    lines.append("")

    return "\n".join(lines)


def save_episode_continuity(script: dict):
    """Extract storylines from today's script and save to GitHub."""
    existing = load_continuity_from_github()
    updated = extract_storylines_from_script(script, existing)
    save_continuity_to_github(updated)


# ---------------------------------------------------------------------------
# Standalone run (for testing or manual update)
# ---------------------------------------------------------------------------

def run():
    """Extract storylines from today's script and update continuity."""
    logger.info("=" * 60)
    logger.info("THE FRICTION — Story Continuity Update")
    logger.info("=" * 60)

    script_path = Path(SCRIPT_INPUT_FILE)
    if not script_path.exists():
        logger.error(f"Script file not found: {SCRIPT_INPUT_FILE}")
        return

    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)

    save_episode_continuity(script)

    # Show current state
    continuity = load_continuity_from_github()
    for s in continuity.get("storylines", []):
        status_icon = "🟢" if s.get("status") == "active" else "💤"
        logger.info(f"  {status_icon} [{s.get('character')}] {s.get('title')}: "
                    f"{s.get('summary', '')[:80]}")

    logger.info("\nDone.")


if __name__ == "__main__":
    run()
