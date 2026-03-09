"""
THE FRICTION — Automated News Ingestion (Phase 1)
==================================================
This script runs at 4:00 AM daily and produces a structured JSON file
containing the day's top stories, categorized and ready to feed into
the AI script generation engine.

Sources:
  - AP News (RSS) — primary wire service
  - Reuters (RSS) — secondary wire service
  - CNN (RSS) — major US outlet
  - Fox News (RSS) — major US outlet (balance)
  - NYT (RSS) — major US outlet
  - WSJ (RSS) — economy/business focus
  - Reddit r/nottheonion (RSS) — offbeat stories for Jax
  - Reddit r/FloridaMan (RSS) — absurd local stories for Jax

Requirements:
  pip install feedparser trafilatura anthropic requests python-dotenv

Setup:
  1. Copy .env.example to .env
  2. Add your Anthropic API key
  3. Run: python news_ingestion.py
  4. Output: daily_news.json in the same directory
"""

import os
import json
import hashlib
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import feedparser
import requests
import trafilatura
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("friction.news")

# How many hours back to look for stories
LOOKBACK_HOURS = 24

# Maximum stories to pull per feed before filtering
MAX_PER_FEED = 15

# Final output targets
TARGET_MAIN_STORIES = 3        # Geopolitics, Economy, Domestic
TARGET_OFFBEAT_STORIES = 4     # For Jax's Rapid Fire

# User agent for fetching
USER_AGENT = "TheFriction-NewsBot/1.0 (podcast news aggregator)"

# Anthropic API key (for the optional AI pre-processing step)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Dropbox for story memory persistence
DROPBOX_TOKEN = os.getenv("DROPBOX_TOKEN", "")
STORY_MEMORY_FILE = "/story_memory.json"  # Path in Dropbox app folder
MEMORY_DAYS = 7  # How many days of history to keep

# Output file
OUTPUT_FILE = os.getenv("NEWS_OUTPUT_FILE", "daily_news.json")


# ---------------------------------------------------------------------------
# RSS Feed Definitions
# ---------------------------------------------------------------------------

FEEDS = {
    # --- Wire Services (highest authority) ---
    "ap_top": {
        "url": "https://rsshub.app/apnews/topics/apf-topnews",
        "source": "AP News",
        "authority": 10,
        "category_hint": "general",
        "type": "main",
    },
    "ap_world": {
        "url": "https://rsshub.app/apnews/topics/apf-WorldNews",
        "source": "AP News",
        "authority": 10,
        "category_hint": "geopolitics",
        "type": "main",
    },
    "reuters_world": {
        "url": "https://www.reutersagency.com/feed/?best-topics=world&post_type=best",
        "source": "Reuters",
        "authority": 10,
        "category_hint": "geopolitics",
        "type": "main",
    },
    "reuters_business": {
        "url": "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best",
        "source": "Reuters",
        "authority": 10,
        "category_hint": "economy",
        "type": "main",
    },

    # --- Major US Outlets ---
    "cnn_top": {
        "url": "http://rss.cnn.com/rss/cnn_topstories.rss",
        "source": "CNN",
        "authority": 7,
        "category_hint": "general",
        "type": "main",
    },
    "cnn_world": {
        "url": "http://rss.cnn.com/rss/cnn_world.rss",
        "source": "CNN",
        "authority": 7,
        "category_hint": "geopolitics",
        "type": "main",
    },
    "fox_latest": {
        "url": "https://moxie.foxnews.com/google-publisher/latest.xml",
        "source": "Fox News",
        "authority": 7,
        "category_hint": "general",
        "type": "main",
    },
    "fox_politics": {
        "url": "https://moxie.foxnews.com/google-publisher/politics.xml",
        "source": "Fox News",
        "authority": 7,
        "category_hint": "domestic",
        "type": "main",
    },
    "nyt_home": {
        "url": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
        "source": "New York Times",
        "authority": 8,
        "category_hint": "general",
        "type": "main",
    },
    "nyt_world": {
        "url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        "source": "New York Times",
        "authority": 8,
        "category_hint": "geopolitics",
        "type": "main",
    },
    "nyt_business": {
        "url": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
        "source": "New York Times",
        "authority": 8,
        "category_hint": "economy",
        "type": "main",
    },
    "wsj_world": {
        "url": "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
        "source": "Wall Street Journal",
        "authority": 9,
        "category_hint": "geopolitics",
        "type": "main",
    },
    "wsj_markets": {
        "url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
        "source": "Wall Street Journal",
        "authority": 9,
        "category_hint": "economy",
        "type": "main",
    },

    # --- Offbeat / Weird (for Jax) ---
    "nottheonion": {
        "url": "https://www.reddit.com/r/nottheonion/.rss",
        "source": "Reddit r/nottheonion",
        "authority": 3,
        "category_hint": "offbeat",
        "type": "offbeat",
    },
    "floridaman": {
        "url": "https://www.reddit.com/r/FloridaMan/.rss",
        "source": "Reddit r/FloridaMan",
        "authority": 3,
        "category_hint": "offbeat",
        "type": "offbeat",
    },
}

# Backup feeds in case primary RSS feeds are down or changed
BACKUP_FEEDS = {
    "ap_backup": {
        "url": "https://news.google.com/rss/search?q=site:apnews.com&hl=en-US&gl=US&ceid=US:en",
        "source": "AP News (via Google)",
        "authority": 9,
        "category_hint": "general",
        "type": "main",
    },
    "reuters_backup": {
        "url": "https://news.google.com/rss/search?q=site:reuters.com&hl=en-US&gl=US&ceid=US:en",
        "source": "Reuters (via Google)",
        "authority": 9,
        "category_hint": "general",
        "type": "main",
    },
    "google_world": {
        "url": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx1YlY4U0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US:en",
        "source": "Google News World",
        "authority": 6,
        "category_hint": "geopolitics",
        "type": "main",
    },
    "google_business": {
        "url": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx6TVdZU0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US:en",
        "source": "Google News Business",
        "authority": 6,
        "category_hint": "economy",
        "type": "main",
    },
}


# ---------------------------------------------------------------------------
# Story Representation
# ---------------------------------------------------------------------------

class Story:
    """Represents a single news story from any source."""

    def __init__(
        self,
        title: str,
        url: str,
        source: str,
        published: Optional[datetime],
        description: str = "",
        full_text: str = "",
        authority: int = 5,
        category_hint: str = "general",
        story_type: str = "main",
    ):
        self.title = title.strip()
        self.url = url.strip()
        self.source = source
        self.published = published
        self.description = description.strip()
        self.full_text = full_text
        self.authority = authority
        self.category_hint = category_hint
        self.story_type = story_type  # "main" or "offbeat"

        # Assigned during categorization
        self.final_category = ""
        self.mention_count = 1  # How many feeds this story appeared in
        self.score = 0.0

    @property
    def fingerprint(self) -> str:
        """Generate a fingerprint for deduplication based on title keywords."""
        # Normalize: lowercase, remove punctuation, take first 8 significant words
        import re
        words = re.sub(r'[^\w\s]', '', self.title.lower()).split()
        # Remove common stop words
        stop = {'the','a','an','in','on','at','to','for','of','and','is','are','was','were',
                'be','been','has','have','had','with','from','by','that','this','it','its'}
        significant = [w for w in words if w not in stop][:8]
        key = ' '.join(sorted(significant))
        return hashlib.md5(key.encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "published": self.published.isoformat() if self.published else None,
            "description": self.description,
            "full_text": self.full_text[:5000],  # Cap at 5000 chars to manage token costs
            "category": self.final_category,
            "authority": self.authority,
            "mention_count": self.mention_count,
            "score": round(self.score, 2),
        }


# ---------------------------------------------------------------------------
# Phase 1A: Fetch RSS Feeds
# ---------------------------------------------------------------------------

def fetch_feed(feed_id: str, feed_config: dict) -> list[Story]:
    """Fetch and parse a single RSS feed, returning a list of Story objects."""
    url = feed_config["url"]
    logger.info(f"Fetching feed: {feed_id} ({feed_config['source']})")

    try:
        # feedparser handles most RSS/Atom variations
        feed = feedparser.parse(
            url,
            agent=USER_AGENT,
            request_headers={"Accept": "application/rss+xml, application/xml, text/xml"},
        )

        if feed.bozo and not feed.entries:
            logger.warning(f"Feed {feed_id} returned errors and no entries: {feed.bozo_exception}")
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
        stories = []

        for entry in feed.entries[:MAX_PER_FEED]:
            # Parse published date
            published = None
            for date_field in ('published_parsed', 'updated_parsed', 'created_parsed'):
                parsed = getattr(entry, date_field, None)
                if parsed:
                    try:
                        from time import mktime
                        published = datetime.fromtimestamp(mktime(parsed), tz=timezone.utc)
                    except (ValueError, OverflowError, OSError):
                        pass
                    break

            # Skip old stories
            if published and published < cutoff:
                continue

            # Extract title and URL
            title = getattr(entry, 'title', '').strip()
            link = getattr(entry, 'link', '').strip()

            if not title or not link:
                continue

            # Extract description/summary
            description = getattr(entry, 'summary', '') or getattr(entry, 'description', '')
            # Clean HTML from description
            if description:
                import re
                description = re.sub(r'<[^>]+>', '', description).strip()

            story = Story(
                title=title,
                url=link,
                source=feed_config["source"],
                published=published,
                description=description[:500],
                authority=feed_config["authority"],
                category_hint=feed_config["category_hint"],
                story_type=feed_config["type"],
            )
            stories.append(story)

        logger.info(f"  -> {len(stories)} stories from {feed_id}")
        return stories

    except Exception as e:
        logger.error(f"Failed to fetch feed {feed_id}: {e}")
        return []


def fetch_all_feeds() -> list[Story]:
    """Fetch all configured feeds and return combined story list."""
    all_stories = []

    # Try primary feeds first
    for feed_id, config in FEEDS.items():
        stories = fetch_feed(feed_id, config)
        all_stories.extend(stories)

    # If we got very few main stories, try backup feeds
    main_count = sum(1 for s in all_stories if s.story_type == "main")
    if main_count < 10:
        logger.warning(f"Only {main_count} main stories from primary feeds. Trying backups...")
        for feed_id, config in BACKUP_FEEDS.items():
            stories = fetch_feed(feed_id, config)
            all_stories.extend(stories)

    logger.info(f"Total raw stories fetched: {len(all_stories)}")
    return all_stories


# ---------------------------------------------------------------------------
# Phase 1B: Deduplicate
# ---------------------------------------------------------------------------

def deduplicate(stories: list[Story]) -> list[Story]:
    """
    Merge duplicate stories (same event across outlets).
    Keeps the highest-authority version but increments mention_count.
    """
    fingerprint_map: dict[str, Story] = {}

    for story in stories:
        fp = story.fingerprint
        if fp in fingerprint_map:
            existing = fingerprint_map[fp]
            existing.mention_count += 1
            # Keep the higher-authority version
            if story.authority > existing.authority:
                story.mention_count = existing.mention_count
                fingerprint_map[fp] = story
        else:
            fingerprint_map[fp] = story

    deduped = list(fingerprint_map.values())
    logger.info(f"After deduplication: {len(deduped)} unique stories (from {len(stories)} raw)")
    return deduped


# ---------------------------------------------------------------------------
# Phase 1C: Categorize & Score
# ---------------------------------------------------------------------------

# Keyword-based categorization (fast, no API call needed)
CATEGORY_KEYWORDS = {
    "geopolitics": [
        "war", "military", "troops", "nato", "un ", "united nations", "sanctions",
        "diplomacy", "diplomatic", "treaty", "nuclear", "missile", "invasion",
        "ukraine", "russia", "china", "taiwan", "iran", "israel", "palestine",
        "gaza", "hamas", "hezbollah", "north korea", "syria", "middle east",
        "pentagon", "defense", "secretary of state", "foreign minister",
        "ambassador", "cease-fire", "ceasefire", "airstrike", "conflict",
        "refugee", "border", "immigration", "asylum", "tariff", "trade war",
    ],
    "economy": [
        "fed ", "federal reserve", "interest rate", "inflation", "gdp",
        "recession", "stock", "market", "dow", "s&p", "nasdaq", "wall street",
        "jobs report", "unemployment", "hiring", "layoff", "economy", "economic",
        "bank", "banking", "crypto", "bitcoin", "housing", "mortgage",
        "debt", "deficit", "budget", "treasury", "earnings", "profit",
        "ipo", "merger", "acquisition", "trade", "export", "import",
        "oil", "gas price", "energy", "commodity", "supply chain",
    ],
    "domestic": [
        "congress", "senate", "house", "representative", "bill", "legislation",
        "supreme court", "court", "ruling", "governor", "mayor", "election",
        "vote", "ballot", "campaign", "democrat", "republican", "white house",
        "president", "executive order", "fbi", "doj", "justice department",
        "shooting", "gun", "police", "crime", "education", "school",
        "healthcare", "medicare", "medicaid", "social security", "infrastructure",
        "climate", "epa", "environment", "wildfire", "hurricane", "tornado",
        "fda", "vaccine", "pandemic", "public health",
    ],
}

def categorize_story(story: Story) -> str:
    """Assign a category based on keywords in title and description."""
    text = (story.title + " " + story.description).lower()

    scores = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        scores[category] = score

    # Use the feed's category hint as a tiebreaker
    if story.category_hint in scores:
        scores[story.category_hint] += 0.5

    best = max(scores, key=scores.get)
    if scores[best] > 0:
        return best

    # Default to the feed's hint if no keywords matched
    if story.category_hint in ("geopolitics", "economy", "domestic"):
        return story.category_hint

    return "domestic"  # fallback


def score_stories(stories: list[Story], memory: dict = None) -> list[Story]:
    """
    Score and categorize all stories.
    Score = authority * 2 + mention_count * 3 + recency_bonus + repetition_penalty
    Higher score = more important story.
    """
    now = datetime.now(timezone.utc)

    # Load repetition penalties from memory
    repetition_penalties = {}
    if memory:
        try:
            from story_memory import get_penalty_for_headline
            for story in stories:
                penalty = get_penalty_for_headline(story.title, memory)
                if penalty != 0:
                    repetition_penalties[story.title] = penalty
            if repetition_penalties:
                logger.info(f"Repetition penalties applied to {len(repetition_penalties)} stories")
        except Exception as e:
            logger.warning(f"Could not calculate repetition penalties: {e}")

    for story in stories:
        # Categorize
        if story.story_type == "offbeat":
            story.final_category = "offbeat"
        else:
            story.final_category = categorize_story(story)

        # Score
        # Authority (1-10) * 2 = 2-20
        authority_score = story.authority * 2

        # Mention count * 3 (stories covered by multiple outlets are bigger)
        mention_score = story.mention_count * 3

        # Recency bonus: stories from last 6 hours get a boost
        recency_bonus = 0
        if story.published:
            hours_ago = (now - story.published).total_seconds() / 3600
            if hours_ago < 3:
                recency_bonus = 5
            elif hours_ago < 6:
                recency_bonus = 3
            elif hours_ago < 12:
                recency_bonus = 1

        # Repetition penalty (negative score for recently covered topics)
        rep_penalty = repetition_penalties.get(story.title, 0)

        story.score = authority_score + mention_score + recency_bonus + rep_penalty

        if rep_penalty < -3:
            logger.info(f"  Penalized: \"{story.title[:60]}\" (penalty: {rep_penalty:.1f})")

    # Sort by score descending
    stories.sort(key=lambda s: s.score, reverse=True)
    return stories


# ---------------------------------------------------------------------------
# Phase 1D: Select Top Stories
# ---------------------------------------------------------------------------

def select_stories(stories: list[Story]) -> dict:
    """
    Select the final stories for today's episode:
    - 1 Geopolitics story
    - 1 Economy story
    - 1 Domestic story
    - 3-4 Offbeat stories (for Jax)
    """
    main_stories = [s for s in stories if s.story_type == "main"]
    offbeat_stories = [s for s in stories if s.story_type == "offbeat"]

    selected = {
        "geopolitics": None,
        "economy": None,
        "domestic": None,
        "offbeat": [],
    }

    # Pick the highest-scored story in each category
    for category in ("geopolitics", "economy", "domestic"):
        candidates = [s for s in main_stories if s.final_category == category]
        if candidates:
            selected[category] = candidates[0]  # Already sorted by score
            logger.info(f"Selected {category}: \"{candidates[0].title}\" (score: {candidates[0].score})")

    # If any category is empty, fill from "general" pool
    for category in ("geopolitics", "economy", "domestic"):
        if selected[category] is None:
            # Grab the next best unused main story
            used_urls = {s.url for s in selected.values() if isinstance(s, Story)}
            remaining = [s for s in main_stories if s.url not in used_urls]
            if remaining:
                selected[category] = remaining[0]
                remaining[0].final_category = category
                logger.warning(f"No {category} stories found; using fallback: \"{remaining[0].title}\"")

    # Pick offbeat stories
    selected["offbeat"] = offbeat_stories[:TARGET_OFFBEAT_STORIES]
    logger.info(f"Selected {len(selected['offbeat'])} offbeat stories")

    return selected


# ---------------------------------------------------------------------------
# Phase 1E: Fetch Full Article Text
# ---------------------------------------------------------------------------

def fetch_full_text(story: Story) -> str:
    """
    Fetch and extract the full article text from the story's URL.
    Uses trafilatura for robust extraction from any website.
    """
    if not story.url:
        return ""

    logger.info(f"Fetching full text: {story.url[:80]}...")

    try:
        # Download the page
        downloaded = trafilatura.fetch_url(
            story.url,
            no_ssl=True,
        )

        if not downloaded:
            logger.warning(f"  -> Could not download: {story.url[:80]}")
            return story.description  # Fall back to RSS description

        # Extract main article text
        text = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
            favor_precision=True,
        )

        if text:
            # Cap at 5000 chars to manage token costs
            text = text[:5000]
            logger.info(f"  -> Extracted {len(text)} chars")
            return text
        else:
            logger.warning(f"  -> Extraction returned empty for: {story.url[:80]}")
            return story.description

    except Exception as e:
        logger.error(f"  -> Error fetching full text: {e}")
        return story.description  # Fall back to description


def enrich_stories(selected: dict) -> dict:
    """Fetch full article text for all selected stories."""
    for category in ("geopolitics", "economy", "domestic"):
        story = selected[category]
        if story:
            story.full_text = fetch_full_text(story)

    for story in selected["offbeat"]:
        # For offbeat, the headline is usually enough; just grab a short excerpt
        story.full_text = fetch_full_text(story) if story.url else story.description

    return selected


# ---------------------------------------------------------------------------
# Phase 1F (Optional): AI Pre-Processing
# ---------------------------------------------------------------------------

def ai_preprocess(selected: dict) -> dict:
    """
    Optional: Run the selected stories through Claude for pre-processing.
    Produces clean summaries, identifies debate angles, suggests episode archetype.
    
    This step costs ~$0.01-0.03 per day and significantly improves script quality.
    Skip if ANTHROPIC_API_KEY is not set.
    """
    if not ANTHROPIC_API_KEY:
        logger.info("No Anthropic API key set; skipping AI pre-processing.")
        return {}

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=ANTHROPIC_API_KEY)
    except ImportError:
        logger.warning("anthropic package not installed; skipping AI pre-processing.")
        return {}
    except Exception as e:
        logger.error(f"Failed to initialize Anthropic client: {e}")
        return {}

    # Build the pre-processing prompt
    stories_text = ""
    for category in ("geopolitics", "economy", "domestic"):
        story = selected[category]
        if story:
            stories_text += f"\n--- {category.upper()} ---\n"
            stories_text += f"Headline: {story.title}\n"
            stories_text += f"Source: {story.source}\n"
            stories_text += f"Text: {story.full_text[:3000]}\n"

    offbeat_text = "\n--- OFFBEAT (for Jax's Rapid Fire) ---\n"
    for story in selected["offbeat"]:
        offbeat_text += f"- {story.title} ({story.source})\n"

    system_prompt = """You are a news editor preparing a briefing for a daily podcast called The Friction.
Your job is to pre-process the day's stories into a structured briefing.

Output ONLY valid JSON with this structure:
{
  "episode_archetype": "crisis|policy|scandal|culture|quiet",
  "archetype_reasoning": "one sentence explaining why",
  "stories": {
    "geopolitics": {
      "summary": "2-3 sentence wire-service-style factual summary",
      "key_entities": ["list of people, countries, organizations, dollar amounts"],
      "debate_angles": ["2-3 potential Bree vs Duke debate angles"],
      "pringle_suggestion": "a suggested historical parallel for Dr. Pringle"
    },
    "economy": { same structure },
    "domestic": { same structure }
  },
  "offbeat_headlines": ["cleaned up versions of the 3-4 best offbeat headlines for Jax"]
}"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            temperature=0.3,  # Low temp for factual summary work
            system=system_prompt,
            messages=[{"role": "user", "content": stories_text + offbeat_text}],
        )

        # Parse the response
        text = response.content[0].text
        # Strip markdown fences if present
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        preprocessed = json.loads(text)
        logger.info(f"AI pre-processing complete. Episode archetype: {preprocessed.get('episode_archetype', 'unknown')}")
        return preprocessed

    except json.JSONDecodeError as e:
        logger.error(f"AI pre-processing returned invalid JSON: {e}")
        return {}
    except Exception as e:
        logger.error(f"AI pre-processing failed: {e}")
        return {}


# ---------------------------------------------------------------------------
# Phase 1G: Assemble Final Output
# ---------------------------------------------------------------------------

def assemble_output(selected: dict, preprocessed: dict) -> dict:
    """
    Assemble the final daily_news.json output that gets fed into
    the script generation engine.
    """
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "episode_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),

        # AI pre-processing results (if available)
        "episode_archetype": preprocessed.get("episode_archetype", ""),
        "archetype_reasoning": preprocessed.get("archetype_reasoning", ""),

        # The three main stories
        "stories": {},

        # Offbeat stories for Jax
        "offbeat": [],

        # Pre-processed insights (if available)
        "preprocessed": preprocessed.get("stories", {}),
        "offbeat_headlines": preprocessed.get("offbeat_headlines", []),
    }

    for category in ("geopolitics", "economy", "domestic"):
        story = selected[category]
        if story:
            output["stories"][category] = story.to_dict()
        else:
            output["stories"][category] = None
            logger.warning(f"No story selected for {category}")

    for story in selected["offbeat"]:
        output["offbeat"].append(story.to_dict())

    return output


# ---------------------------------------------------------------------------
# Main Pipeline
# ---------------------------------------------------------------------------

def run():
    """Execute the full news ingestion pipeline."""
    logger.info("=" * 60)
    logger.info("THE FRICTION — News Ingestion Pipeline")
    logger.info(f"Run time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 60)

    # Load story memory (to avoid repeating topics)
    memory = None
    try:
        from story_memory import load_memory
        logger.info("\n--- Loading story memory ---")
        memory = load_memory()
    except Exception as e:
        logger.warning(f"Could not load story memory (will proceed without it): {e}")

    # Step 1: Fetch all feeds
    logger.info("\n--- PHASE 1A: Fetching RSS feeds ---")
    all_stories = fetch_all_feeds()

    if not all_stories:
        logger.error("FATAL: No stories fetched from any feed. Check network and feed URLs.")
        sys.exit(1)

    # Step 2: Deduplicate
    logger.info("\n--- PHASE 1B: Deduplicating ---")
    unique_stories = deduplicate(all_stories)

    # Step 3: Categorize & Score (with memory-based repetition penalties)
    logger.info("\n--- PHASE 1C: Categorizing & Scoring ---")
    scored_stories = score_stories(unique_stories, memory=memory)

    # Step 4: Select top stories
    logger.info("\n--- PHASE 1D: Selecting top stories ---")
    selected = select_stories(scored_stories)

    # Verify we have at least something
    main_count = sum(1 for k in ("geopolitics", "economy", "domestic") if selected[k])
    if main_count == 0:
        logger.error("FATAL: Could not select any main stories. Aborting.")
        sys.exit(1)

    # Step 5: Fetch full article text
    logger.info("\n--- PHASE 1E: Fetching full article text ---")
    selected = enrich_stories(selected)

    # Step 6: AI pre-processing (optional)
    logger.info("\n--- PHASE 1F: AI pre-processing ---")
    preprocessed = ai_preprocess(selected)

    # Step 7: Assemble output
    logger.info("\n--- PHASE 1G: Assembling output ---")
    output = assemble_output(selected, preprocessed)

    # Write output
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    logger.info(f"\nOutput written to: {OUTPUT_FILE}")
    logger.info(f"Stories selected:")
    for category in ("geopolitics", "economy", "domestic"):
        story = selected[category]
        if story:
            logger.info(f"  {category}: \"{story.title}\" ({story.source}, score: {story.score})")
    logger.info(f"  offbeat: {len(selected['offbeat'])} stories")
    if preprocessed:
        logger.info(f"  Episode archetype: {preprocessed.get('episode_archetype', 'not determined')}")
    logger.info("\nDone.")


if __name__ == "__main__":
    run()
