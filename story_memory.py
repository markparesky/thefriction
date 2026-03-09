"""
THE FRICTION — Story Memory
==============================
Tracks which stories have been covered across episodes.
Saves a small JSON file to GitHub after each run.
The news ingestion script reads it to penalize repeated topics.

The memory file stores the last 14 days of story data:
- Headlines covered
- Keywords from each story
- Dates they were covered

Requirements:
  - GITHUB_TOKEN environment variable
"""

import os
import json
import logging
import re
import requests
import base64
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("friction.memory")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = "markparesky/thefriction"
MEMORY_FILE_PATH = "data/story_memory.json"
MEMORY_DAYS = 30  # How many days of history to keep

SCRIPT_INPUT_FILE = os.getenv("SCRIPT_OUTPUT_FILE", "daily_script.json")
NEWS_INPUT_FILE = os.getenv("NEWS_OUTPUT_FILE", "daily_news.json")


# ---------------------------------------------------------------------------
# Keywords extraction
# ---------------------------------------------------------------------------

STOP_WORDS = {
    'the', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'of', 'and', 'is', 'are',
    'was', 'were', 'be', 'been', 'has', 'have', 'had', 'with', 'from', 'by',
    'that', 'this', 'it', 'its', 'as', 'but', 'or', 'not', 'no', 'if', 'so',
    'up', 'out', 'about', 'into', 'over', 'after', 'before', 'between', 'under',
    'again', 'then', 'there', 'here', 'when', 'where', 'why', 'how', 'all',
    'each', 'every', 'both', 'few', 'more', 'most', 'other', 'some', 'such',
    'than', 'too', 'very', 'can', 'will', 'just', 'should', 'now', 'also',
    'new', 'says', 'said', 'could', 'would', 'may', 'might', 'us', 'we',
    'they', 'he', 'she', 'his', 'her', 'their', 'our', 'my', 'your',
    'who', 'what', 'which', 'do', 'does', 'did', 'been', 'being',
    'first', 'last', 'year', 'years', 'time', 'make', 'get', 'go',
}


def extract_keywords(text: str) -> list[str]:
    """Extract significant keywords from a headline or text."""
    words = re.sub(r'[^\w\s]', '', text.lower()).split()
    keywords = [w for w in words if w not in STOP_WORDS and len(w) > 2]
    return keywords[:10]  # Cap at 10 keywords per story


# ---------------------------------------------------------------------------
# GitHub read/write
# ---------------------------------------------------------------------------

def load_memory_from_github() -> dict:
    """Load the story memory JSON from GitHub."""
    if not GITHUB_TOKEN:
        logger.warning("GITHUB_TOKEN not set. Cannot load memory.")
        return {"episodes": []}

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{MEMORY_FILE_PATH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)

        if response.status_code == 200:
            content_b64 = response.json().get("content", "")
            content = base64.b64decode(content_b64).decode("utf-8")
            memory = json.loads(content)
            logger.info(f"Loaded memory: {len(memory.get('episodes', []))} past episodes")
            return memory

        elif response.status_code == 404:
            logger.info("No memory file found (first run). Starting fresh.")
            return {"episodes": []}

        else:
            logger.warning(f"Could not load memory: {response.status_code}")
            return {"episodes": []}

    except Exception as e:
        logger.warning(f"Error loading memory: {e}")
        return {"episodes": []}


def save_memory_to_github(memory: dict):
    """Save the story memory JSON to GitHub."""
    if not GITHUB_TOKEN:
        logger.warning("GITHUB_TOKEN not set. Cannot save memory.")
        return

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{MEMORY_FILE_PATH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    content_json = json.dumps(memory, indent=2, ensure_ascii=False)
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
        "message": f"Story memory update: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        "content": content_b64,
    }
    if sha:
        payload["sha"] = sha

    try:
        response = requests.put(url, headers=headers, json=payload, timeout=30)
        if response.status_code in (200, 201):
            logger.info("Memory saved to GitHub successfully.")
        else:
            logger.error(f"Failed to save memory: {response.status_code} — {response.text[:200]}")
    except Exception as e:
        logger.error(f"Error saving memory: {e}")


# ---------------------------------------------------------------------------
# Memory operations
# ---------------------------------------------------------------------------

def get_recent_keywords(memory: dict) -> dict[str, int]:
    """
    Get a frequency map of keywords from recent episodes.
    More recent = higher weight. Returns {keyword: weight}.
    """
    keyword_weights = {}
    now = datetime.now(timezone.utc)

    for episode in memory.get("episodes", []):
        ep_date_str = episode.get("date", "")
        try:
            ep_date = datetime.fromisoformat(ep_date_str).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue

        days_ago = (now - ep_date).days
        if days_ago > MEMORY_DAYS:
            continue

        # Weight: more recent = higher penalty
        # Yesterday = weight 5, 7 days ago = weight 2, 14 days ago = weight 1
        weight = max(1, 6 - days_ago)

        for story in episode.get("stories", []):
            for keyword in story.get("keywords", []):
                keyword_weights[keyword] = keyword_weights.get(keyword, 0) + weight

    return keyword_weights


def calculate_repetition_penalty(headline: str, keyword_weights: dict) -> float:
    """
    Calculate how much to penalize a story based on keyword overlap
    with recently covered stories. Returns a negative score adjustment.
    """
    keywords = extract_keywords(headline)
    if not keywords:
        return 0.0

    total_weight = sum(keyword_weights.get(kw, 0) for kw in keywords)
    matching_count = sum(1 for kw in keywords if kw in keyword_weights)

    if matching_count == 0:
        return 0.0  # Completely fresh topic — no penalty

    # Overlap ratio: what fraction of this headline's keywords were recently covered
    overlap_ratio = matching_count / len(keywords)

    # Penalty scales with both overlap and recency weight
    # High overlap + high weight = big penalty (same story, covered yesterday)
    # Low overlap + low weight = small penalty (tangentially related, covered last week)
    penalty = -1 * (overlap_ratio * total_weight)

    return penalty


def record_episode(memory: dict, news: dict, script: dict) -> dict:
    """
    Record today's episode in the memory.
    Returns the updated memory dict.
    """
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    # Extract story info from the news data
    stories_data = []
    for category in ("geopolitics", "economy", "domestic"):
        story = news.get("stories", {}).get(category)
        if story:
            stories_data.append({
                "category": category,
                "headline": story.get("title", ""),
                "source": story.get("source", ""),
                "keywords": extract_keywords(story.get("title", "")),
            })

    # Extract episode archetype
    metadata = script.get("metadata", {})

    episode_record = {
        "date": today,
        "archetype": metadata.get("episode_archetype", ""),
        "stories": stories_data,
    }

    # Add to memory
    memory.setdefault("episodes", []).append(episode_record)

    # Prune old episodes (keep only last MEMORY_DAYS)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=MEMORY_DAYS)).strftime('%Y-%m-%d')
    memory["episodes"] = [
        ep for ep in memory["episodes"]
        if ep.get("date", "") >= cutoff
    ]

    logger.info(f"Recorded episode for {today}. Memory now has {len(memory['episodes'])} episodes.")
    return memory


# ---------------------------------------------------------------------------
# Public interface (called by other scripts)
# ---------------------------------------------------------------------------

def load_memory() -> dict:
    """Load memory and return the keyword weights for scoring."""
    memory = load_memory_from_github()
    return memory


def get_penalty_for_headline(headline: str, memory: dict) -> float:
    """Get the repetition penalty for a given headline."""
    keyword_weights = get_recent_keywords(memory)
    return calculate_repetition_penalty(headline, keyword_weights)


def save_episode_to_memory(news: dict, script: dict):
    """Record today's episode and save to GitHub."""
    memory = load_memory_from_github()
    memory = record_episode(memory, news, script)
    save_memory_to_github(memory)


# ---------------------------------------------------------------------------
# Standalone run (for testing)
# ---------------------------------------------------------------------------

def run():
    """Record today's episode in memory. Called after script generation."""
    logger.info("=" * 60)
    logger.info("THE FRICTION — Story Memory Update")
    logger.info("=" * 60)

    # Load today's news and script
    news_path = Path(NEWS_INPUT_FILE)
    script_path = Path(SCRIPT_INPUT_FILE)

    if not news_path.exists():
        logger.error(f"News file not found: {NEWS_INPUT_FILE}")
        return
    if not script_path.exists():
        logger.error(f"Script file not found: {SCRIPT_INPUT_FILE}")
        return

    with open(news_path, "r", encoding="utf-8") as f:
        news = json.load(f)
    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)

    save_episode_to_memory(news, script)

    # Show current memory state
    memory = load_memory_from_github()
    keyword_weights = get_recent_keywords(memory)
    top_keywords = sorted(keyword_weights.items(), key=lambda x: x[1], reverse=True)[:20]
    logger.info(f"\nTop recent keywords (higher = more covered):")
    for kw, weight in top_keywords:
        logger.info(f"  {kw}: {weight}")

    logger.info("\nDone.")


if __name__ == "__main__":
    run()
