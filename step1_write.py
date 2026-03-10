# THE FRICTION - Step 1: Write
# Fetches news, generates script, saves to Dropbox, emails status

import os
import json
import logging
import sys
import requests
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("friction.write")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
DROPBOX_TOKEN = os.getenv("DROPBOX_TOKEN", "")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "")

def send_status_email(subject, body_text):
    if not RESEND_API_KEY or not NOTIFY_EMAIL:
        return
    try:
        requests.post("https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": "The Friction <onboarding@resend.dev>", "to": [NOTIFY_EMAIL],
                  "subject": subject, "html": f"<pre style='font-family:monospace;font-size:14px;'>{body_text}</pre>"})
    except Exception as e:
        logger.error(f"Email failed: {e}")

def save_to_dropbox(data_bytes, path, content_type="application/octet-stream"):
    if not DROPBOX_TOKEN:
        logger.error("DROPBOX_TOKEN not set")
        return False
    try:
        resp = requests.post("https://content.dropboxapi.com/2/files/upload",
            headers={"Authorization": f"Bearer {DROPBOX_TOKEN}",
                     "Dropbox-API-Arg": json.dumps({"path": path, "mode": "overwrite", "autorename": False}),
                     "Content-Type": "application/octet-stream"},
            data=data_bytes, timeout=120)
        if resp.status_code == 200:
            logger.info(f"Saved to Dropbox: {path}")
            return True
        else:
            logger.error(f"Dropbox save failed: {resp.status_code} - {resp.text[:300]}")
            return False
    except Exception as e:
        logger.error(f"Dropbox error: {e}")
        return False

def get_dropbox_link(path):
    if not DROPBOX_TOKEN:
        return ""
    try:
        resp = requests.post("https://api.dropboxapi.com/2/sharing/create_shared_link_with_settings",
            headers={"Authorization": f"Bearer {DROPBOX_TOKEN}", "Content-Type": "application/json"},
            json={"path": path, "settings": {"requested_visibility": "public"}}, timeout=30)
        if resp.status_code == 200:
            return resp.json().get("url", "").replace("dl=0", "dl=1")
        elif resp.status_code == 409:
            resp2 = requests.post("https://api.dropboxapi.com/2/sharing/list_shared_links",
                headers={"Authorization": f"Bearer {DROPBOX_TOKEN}", "Content-Type": "application/json"},
                json={"path": path, "direct_only": True}, timeout=30)
            if resp2.status_code == 200:
                links = resp2.json().get("links", [])
                if links:
                    return links[0].get("url", "").replace("dl=0", "dl=1")
    except Exception as e:
        logger.error(f"Dropbox link error: {e}")
    return ""

def fetch_news():
    import news_ingestion
    news_ingestion.run()
    with open("daily_news.json", "r", encoding="utf-8") as f:
        return json.load(f)

def generate_script(news):
    system_prompt_path = Path("system_prompt.txt")
    if not system_prompt_path.exists():
        logger.error("system_prompt.txt not found")
        sys.exit(1)
    system_prompt = system_prompt_path.read_text(encoding="utf-8")
    logger.info(f"System prompt: {len(system_prompt.split())} words")

    # Build user message
    parts = [f"Today's date: {news.get('episode_date', datetime.now().strftime('%Y-%m-%d'))}", ""]
    if news.get("episode_archetype"):
        parts.append(f"SUGGESTED EPISODE ARCHETYPE: {news['episode_archetype']}")
        parts.append("")
    stories = news.get("stories", {})
    preprocessed = news.get("preprocessed", {})
    for category in ("geopolitics", "economy", "domestic"):
        story = stories.get(category)
        if not story:
            continue
        parts.append(f"--- {category.upper()} ---")
        parts.append(f"Headline: {story.get('title', '')}")
        parts.append(f"Source: {story.get('source', '')}")
        pre = preprocessed.get(category, {})
        if pre.get("summary"):
            parts.append(f"Summary: {pre['summary']}")
        if pre.get("debate_angles"):
            parts.append(f"Debate angles: {'; '.join(pre['debate_angles'])}")
        if pre.get("pringle_suggestion"):
            parts.append(f"Pringle suggestion: {pre['pringle_suggestion']}")
        full_text = story.get("full_text", story.get("description", ""))
        if full_text:
            parts.append(f"Full text: {full_text[:3000]}")
        parts.append("")
    offbeat_headlines = news.get("offbeat_headlines", [])
    offbeat = news.get("offbeat", [])
    parts.append("--- OFFBEAT STORIES (for Jax) ---")
    if offbeat_headlines:
        for h in offbeat_headlines:
            parts.append(f"- {h}")
    elif offbeat:
        for s in offbeat:
            parts.append(f"- {s.get('title', '')} ({s.get('source', '')})")
    parts.append("")
    parts.append("Write today's complete episode script now.")
    user_message = "\n".join(parts)
    logger.info(f"User message: {len(user_message.split())} words")

    # Call Claude API
    logger.info("Calling Claude API...")
    for attempt in range(1, 3):
        try:
            resp = requests.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": "claude-sonnet-4-20250514", "max_tokens": 12000,
                      "temperature": 0.85, "system": system_prompt,
                      "messages": [{"role": "user", "content": user_message}]},
                timeout=300)
            logger.info(f"Response: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                raw_text = ""
                for block in data.get("content", []):
                    if block.get("type") == "text":
                        raw_text += block.get("text", "")
                usage = data.get("usage", {})
                logger.info(f"Tokens: {usage.get('input_tokens', '?')} in, {usage.get('output_tokens', '?')} out")
                # Parse JSON
                text = raw_text.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                if text.endswith("```"):
                    text = text.rsplit("```", 1)[0]
                text = text.strip()
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    import re
                    match = re.search(r'\{[\s\S]*\}', text)
                    if match:
                        try:
                            return json.loads(match.group())
                        except json.JSONDecodeError:
                            pass
                logger.warning(f"Could not parse JSON (attempt {attempt})")
            elif resp.status_code == 401:
                logger.error("ANTHROPIC API KEY INVALID OR NO CREDITS")
                send_status_email("FRICTION FAILED: Anthropic API Error",
                    f"Status: {resp.status_code}\n\nCheck your Anthropic API key and billing.\n\n{resp.text[:500]}")
                sys.exit(1)
            elif resp.status_code == 429:
                logger.error("ANTHROPIC RATE LIMITED - may need to wait or check billing")
                send_status_email("FRICTION FAILED: Anthropic Rate Limited",
                    f"Status: {resp.status_code}\n\nYou may be out of credits or rate limited.\n\n{resp.text[:500]}")
                sys.exit(1)
            else:
                logger.error(f"API error: {resp.status_code} - {resp.text[:300]}")
        except requests.exceptions.Timeout:
            logger.error("API call timed out")
        except Exception as e:
            logger.error(f"API call failed: {e}")
        if attempt < 2:
            import time
            time.sleep(10)
    logger.error("All script generation attempts failed")
    sys.exit(1)

def main():
    logger.info("=" * 60)
    logger.info("THE FRICTION - Step 1: Write")
    logger.info(f"Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 60)

    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    # Phase 1: News
    logger.info("\nFetching news...")
    try:
        news = fetch_news()
    except Exception as e:
        logger.error(f"News fetch failed: {e}")
        send_status_email(f"FRICTION FAILED: News | {date_str}", f"Error: {e}")
        sys.exit(1)

    # Phase 2: Script
    logger.info("\nGenerating script...")
    try:
        script = generate_script(news)
    except SystemExit:
        raise
    except Exception as e:
        logger.error(f"Script generation failed: {e}")
        send_status_email(f"FRICTION FAILED: Script | {date_str}", f"Error: {e}")
        sys.exit(1)

    # Validate
    lines = script.get("script", [])
    metadata = script.get("metadata", {})
    total_words = sum(len((l.get("text") or "").split()) for l in lines)
    logger.info(f"Script: {len(lines)} lines, ~{total_words} words, ~{total_words/170:.1f} minutes")

    # Save script to Dropbox
    script_json = json.dumps(script, indent=2, ensure_ascii=False)
    dropbox_path = f"/scripts/friction_{date_str}.json"
    saved = save_to_dropbox(script_json.encode("utf-8"), dropbox_path)

    # Also save locally for other steps
    with open("daily_script.json", "w", encoding="utf-8") as f:
        f.write(script_json)

    # Get link
    script_link = get_dropbox_link(dropbox_path) if saved else ""

    # Build summary
    headlines = metadata.get("headlines", {})
    summary = f"""THE FRICTION - Script Generated
Date: {date_str}
Archetype: {metadata.get('episode_archetype', '?')}

Headlines:
  Geo: {headlines.get('geopolitics', '?')}
  Econ: {headlines.get('economy', '?')}
  Dom: {headlines.get('domestic', '?')}

Script: {len(lines)} lines, ~{total_words} words, ~{total_words/170:.1f} min
Daily Do: {metadata.get('daily_do', '?')[:100]}

Script saved to Dropbox: {dropbox_path}
Link: {script_link or 'no link'}
"""

    send_status_email(f"FRICTION Script Ready | {date_str} | {headlines.get('geopolitics', '')[:40]}", summary)
    logger.info("\nStep 1 complete.")

if __name__ == "__main__":
    main()
