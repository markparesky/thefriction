"""
THE FRICTION — Email Notification
===================================
Sends the generated episode script via email using Resend.
Called after Phase 2 (script generation) completes.

Requirements:
  - RESEND_API_KEY environment variable
  - NOTIFY_EMAIL environment variable
  - daily_script.json from Phase 2
"""

import os
import json
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("friction.notify")

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "")
SCRIPT_INPUT_FILE = os.getenv("SCRIPT_OUTPUT_FILE", "daily_script.json")
EPISODE_FILE = os.getenv("EPISODE_OUTPUT_FILE", "episode.mp3")


GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = "markparesky/thefriction"


def upload_audio_file() -> str:
    """
    Upload the episode MP3 to the GitHub repository.
    Returns the download URL, or empty string on failure.
    """
    path = Path(EPISODE_FILE)
    if not path.exists():
        logger.warning(f"Episode file not found: {EPISODE_FILE}")
        return ""

    file_size_mb = path.stat().st_size / 1024 / 1024
    logger.info(f"Episode file found: {file_size_mb:.1f} MB")

    if not GITHUB_TOKEN:
        logger.warning("GITHUB_TOKEN not set. Cannot upload to GitHub.")
        logger.warning("Add GITHUB_TOKEN to your Railway variables.")
        return ""

    logger.info(f"GITHUB_TOKEN starts with: {GITHUB_TOKEN[:8]}...")

    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    filename = f"episodes/friction_{date_str}.mp3"
    logger.info(f"Uploading to GitHub: {filename}")

    # GitHub API has practical limits on base64 uploads (~25MB encoded)
    # For files over 20MB raw, this may fail
    if file_size_mb > 50:
        logger.warning(f"File is very large ({file_size_mb:.1f} MB). GitHub upload may fail.")

    try:
        import base64

        logger.info("  Reading and encoding file...")
        with open(path, "rb") as f:
            content_b64 = base64.b64encode(f.read()).decode("utf-8")
        encoded_size_mb = len(content_b64) / 1024 / 1024
        logger.info(f"  Base64 encoded size: {encoded_size_mb:.1f} MB")

        check_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }

        # Check if file already exists
        logger.info("  Checking if file already exists...")
        sha = None
        try:
            check_resp = requests.get(check_url, headers=headers, timeout=30)
            logger.info(f"  Check response: {check_resp.status_code}")
            if check_resp.status_code == 200:
                sha = check_resp.json().get("sha")
                logger.info(f"  File exists, SHA: {sha[:12]}...")
            elif check_resp.status_code == 404:
                logger.info("  File does not exist yet (will create).")
            else:
                logger.warning(f"  Unexpected check response: {check_resp.status_code} — {check_resp.text[:200]}")
        except Exception as e:
            logger.warning(f"  Check request failed: {e}")

        # Upload file
        logger.info("  Uploading to GitHub (this may take 1-2 minutes)...")
        payload = {
            "message": f"Episode: {date_str}",
            "content": content_b64,
        }
        if sha:
            payload["sha"] = sha

        response = requests.put(
            check_url,
            headers=headers,
            json=payload,
            timeout=300,  # 5 minute timeout for large files
        )

        logger.info(f"  Upload response: {response.status_code}")

        if response.status_code in (200, 201):
            download_url = response.json().get("content", {}).get("download_url", "")
            logger.info(f"  Upload successful: {download_url}")
            return download_url
        else:
            error_msg = response.text[:500]
            logger.error(f"  GitHub upload failed: {response.status_code}")
            logger.error(f"  Error: {error_msg}")

            # Common errors
            if response.status_code == 401:
                logger.error("  CAUSE: GITHUB_TOKEN is invalid or expired. Regenerate it.")
            elif response.status_code == 403:
                logger.error("  CAUSE: Token lacks 'Contents: write' permission, or rate limited.")
            elif response.status_code == 422:
                logger.error("  CAUSE: File may be too large for GitHub API, or SHA mismatch.")

            return ""

    except requests.exceptions.Timeout:
        logger.error("  Upload timed out after 300 seconds. File may be too large.")
        return ""
    except MemoryError:
        logger.error("  Out of memory while encoding file. File too large for available RAM.")
        return ""
    except Exception as e:
        logger.error(f"  Upload error: {type(e).__name__}: {e}")
        import traceback
        logger.error(f"  Traceback: {traceback.format_exc()}")
        return ""


def format_script_as_html(script: dict) -> str:
    """Convert the JSON script into a readable HTML email."""
    metadata = script.get("metadata", {})
    lines = script.get("script", [])
    headlines = metadata.get("headlines", {})

    # Character colors for readability
    colors = {
        "LEO": "#1B2A4A",      # navy
        "PRINGLE": "#6B4C9A",  # purple
        "BREE": "#2E7D32",     # green
        "DUKE": "#C0392B",     # red
        "JAX": "#E67E22",      # orange
    }

    html = []
    html.append("""
    <html><body style="font-family: Georgia, serif; max-width: 700px; margin: 0 auto; padding: 20px; background: #f9f9f9;">
    <div style="background: #1B2A4A; color: white; padding: 20px 30px; text-align: center;">
        <h1 style="margin: 0; font-size: 28px;">THE FRICTION</h1>
        <p style="margin: 5px 0 0 0; font-size: 14px; color: #aaa;">Daily Episode Script</p>
    </div>
    <div style="background: white; padding: 30px; border: 1px solid #ddd;">
    """)

    # Metadata summary
    date = metadata.get("episode_date", datetime.now().strftime("%Y-%m-%d"))
    archetype = metadata.get("episode_archetype", "unknown").upper()
    pringle_mode = metadata.get("pringle_mode", "unknown").replace("_", " ").title()

    html.append(f'<h2 style="color: #C0392B; margin-top: 0;">Episode: {date}</h2>')
    html.append(f'<p style="color: #666;"><strong>Archetype:</strong> {archetype} &nbsp;|&nbsp; <strong>Pringle Mode:</strong> {pringle_mode}</p>')

    # Headlines
    html.append('<div style="background: #f0f0f0; padding: 15px; margin: 15px 0; border-left: 4px solid #1B2A4A;">')
    html.append('<p style="margin: 0 0 5px 0; font-weight: bold; color: #1B2A4A;">TODAY\'S HEADLINES</p>')
    for cat in ("geopolitics", "economy", "domestic"):
        headline = headlines.get(cat, "—")
        html.append(f'<p style="margin: 3px 0; color: #333;"><strong>{cat.upper()}:</strong> {headline}</p>')
    html.append('</div>')

    # Daily Do
    daily_do = metadata.get("daily_do", "")
    if daily_do:
        html.append(f'<div style="background: #E8F5E9; padding: 15px; margin: 15px 0; border-left: 4px solid #2E7D32;">')
        html.append(f'<p style="margin: 0; font-weight: bold; color: #2E7D32;">DAILY DO</p>')
        html.append(f'<p style="margin: 5px 0 0 0; color: #333;">{daily_do}</p>')
        html.append('</div>')

    # Word count and timing
    total_words = sum(len(line.get("text", "").split()) for line in lines)
    est_minutes = total_words / 170

    html.append(f'<p style="color: #999; font-size: 13px;">{len(lines)} lines &nbsp;|&nbsp; ~{total_words} words &nbsp;|&nbsp; ~{est_minutes:.1f} minutes</p>')
    html.append('<hr style="border: none; border-top: 2px solid #C0392B; margin: 20px 0;">')

    # The actual script
    current_segment = ""
    segment_names = {
        "cold_open": "COLD OPEN",
        "brief": "THE BRIEF",
        "deep_dive": "THE DEEP DIVE",
        "pringle": "THE PRINGLE PERSPECTIVE",
        "rapid_fire": "THE RAPID FIRE",
        "daily_do": "THE DAILY DO & OUTRO",
    }

    for line in lines:
        segment = line.get("segment", "")
        character = line.get("character", "UNKNOWN")
        text = line.get("text", "")
        direction = line.get("direction", "")

        # Segment header
        if segment != current_segment:
            current_segment = segment
            seg_name = segment_names.get(segment, segment.upper())
            html.append(f'<h3 style="color: #1B2A4A; margin-top: 25px; border-bottom: 1px solid #ddd; padding-bottom: 5px;">{seg_name}</h3>')

        # Character line
        color = colors.get(character, "#333")
        direction_text = f' <span style="color: #999; font-style: italic; font-size: 13px;">({direction})</span>' if direction else ""

        html.append(f'<p style="margin: 8px 0; line-height: 1.6;">')
        html.append(f'<strong style="color: {color};">{character}:</strong>{direction_text} {text}')
        html.append(f'</p>')

    # Clips
    clips = metadata.get("clips", [])
    if clips:
        html.append('<hr style="border: none; border-top: 2px solid #C0392B; margin: 20px 0;">')
        html.append('<h3 style="color: #1B2A4A;">FLAGGED CLIPS</h3>')
        for i, clip in enumerate(clips, 1):
            html.append(f'<p style="color: #666; font-size: 14px;"><strong>Clip {i}:</strong> Lines {clip.get("start_line", "?")}-{clip.get("end_line", "?")} | {clip.get("platform", "?")} | "{clip.get("caption", "")}"</p>')

    html.append("""
    </div>
    <div style="text-align: center; padding: 15px; color: #999; font-size: 12px;">
        <p>THE FRICTION — Automated Daily Pipeline</p>
    </div>
    </body></html>
    """)

    return "\n".join(html)


def send_email(subject: str, html_body: str):
    """Send an email via Resend API."""
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set. Skipping email notification.")
        return False

    if not NOTIFY_EMAIL:
        logger.warning("NOTIFY_EMAIL not set. Skipping email notification.")
        return False

    try:
        response = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": "The Friction <onboarding@resend.dev>",
                "to": [NOTIFY_EMAIL],
                "subject": subject,
                "html": html_body,
            },
        )

        if response.status_code == 200:
            logger.info(f"Email sent successfully to {NOTIFY_EMAIL}")
            return True
        else:
            logger.error(f"Email failed: {response.status_code} — {response.text}")
            return False

    except Exception as e:
        logger.error(f"Email send error: {e}")
        return False


def run():
    """Load the script, upload audio, and email everything."""
    logger.info("=" * 60)
    logger.info("THE FRICTION — Email Notification")
    logger.info("=" * 60)

    # Upload audio file first
    audio_link = upload_audio_file()

    # Load script
    path = Path(SCRIPT_INPUT_FILE)
    if not path.exists():
        logger.error(f"Script file not found: {SCRIPT_INPUT_FILE}")
        logger.error("Skipping email notification.")
        return

    with open(path, "r", encoding="utf-8") as f:
        script = json.load(f)

    # Build email
    metadata = script.get("metadata", {})
    date = metadata.get("episode_date", datetime.now().strftime("%Y-%m-%d"))
    archetype = metadata.get("episode_archetype", "")
    headlines = metadata.get("headlines", {})
    top_headline = headlines.get("geopolitics", headlines.get("economy", headlines.get("domestic", "Today's Episode")))

    subject = f"THE FRICTION | {date} | {top_headline[:60]}"

    logger.info(f"Building email for {date}...")
    html_body = format_script_as_html(script)

    # Add audio download link at the top of the email
    if audio_link:
        audio_banner = f"""
        <div style="background: #C0392B; color: white; padding: 15px 20px; margin-bottom: 20px; text-align: center; font-family: Georgia, serif;">
            <p style="margin: 0 0 10px 0; font-size: 18px; font-weight: bold;">LISTEN TO TODAY'S EPISODE</p>
            <a href="{audio_link}" style="background: white; color: #C0392B; padding: 10px 30px; text-decoration: none; font-weight: bold; font-size: 16px; display: inline-block;">
                DOWNLOAD MP3
            </a>
            <p style="margin: 10px 0 0 0; font-size: 12px; color: #ffaaaa;">Link expires after 5 downloads or 14 days</p>
        </div>
        """
        # Insert after the opening white div
        html_body = html_body.replace(
            '<div style="background: white; padding: 30px; border: 1px solid #ddd;">',
            f'<div style="background: white; padding: 30px; border: 1px solid #ddd;">\n{audio_banner}',
            1
        )

    logger.info(f"Email body: {len(html_body)} characters")
    if audio_link:
        logger.info(f"Audio download link included: {audio_link}")

    # Send
    send_email(subject, html_body)

    logger.info("Done.")


if __name__ == "__main__":
    run()
