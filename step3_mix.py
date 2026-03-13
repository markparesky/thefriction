# THE FRICTION - Step 3: Mix
# Downloads audio clips from GitHub, mixes with music, emails episode

import os
import json
import logging
import sys
import random
import base64
import requests
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("friction.mix")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = "markparesky/thefriction"
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "")

def send_status_email(subject, body_text):
    if not RESEND_API_KEY or not NOTIFY_EMAIL:
        return
    try:
        requests.post("https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": "The Friction <onboarding@resend.dev>", "to": [NOTIFY_EMAIL],
                  "subject": subject, "html": f"<pre style='font-family:monospace;font-size:14px;'>{body_text}</pre>"},
            timeout=30)
    except Exception as e:
        logger.error(f"Email failed: {e}")

def send_episode_email(subject, body_text, mp3_path):
    if not RESEND_API_KEY or not NOTIFY_EMAIL:
        return False
    try:
        with open(mp3_path, "rb") as f:
            mp3_data = f.read()
        mp3_b64 = base64.b64encode(mp3_data).decode("utf-8")
        filename = os.path.basename(mp3_path)
        resp = requests.post("https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={
                "from": "The Friction <onboarding@resend.dev>",
                "to": [NOTIFY_EMAIL],
                "subject": subject,
                "html": f"<pre style='font-family:monospace;font-size:14px;'>{body_text}</pre>",
                "attachments": [{"filename": filename, "content": mp3_b64}]
            },
            timeout=120)
        if resp.status_code == 200:
            logger.info("Episode email sent with attachment!")
            return True
        else:
            logger.error(f"Episode email failed: {resp.status_code} - {resp.text[:300]}")
            return False
    except Exception as e:
        logger.error(f"Episode email error: {e}")
        return False

def download_from_github(filepath):
    if not GITHUB_TOKEN:
        return None
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filepath}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        resp = requests.get(url, headers=headers, timeout=60)
        if resp.status_code == 200:
            return base64.b64decode(resp.json().get("content", ""))
        else:
            logger.error(f"GitHub download failed ({filepath}): {resp.status_code}")
            return None
    except Exception as e:
        logger.error(f"GitHub download error: {e}")
        return None

def list_github_folder(folder_path):
    if not GITHUB_TOKEN:
        return []
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{folder_path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            files = resp.json()
            return [f.get("path", "") for f in files if f.get("name", "").endswith(".mp3")]
        else:
            logger.error(f"GitHub list failed ({folder_path}): {resp.status_code}")
            return []
    except Exception as e:
        logger.error(f"GitHub list error: {e}")
        return []

def main():
    logger.info("=" * 60)
    logger.info("THE FRICTION - Step 3: Mix")
    logger.info(f"Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 60)

    from pydub import AudioSegment

    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    # Download script from GitHub
    filename = f"friction_{date_str}.json"
    logger.info(f"\nDownloading script: scripts/{filename}")
    script_data = download_from_github(f"scripts/{filename}")
    if not script_data:
        send_status_email(f"FRICTION FAILED: No Script | {date_str}",
            f"Could not find scripts/{filename}")
        sys.exit(1)

    script = json.loads(script_data.decode("utf-8"))
    lines = script.get("script", [])
    logger.info(f"Script: {len(lines)} lines")

    # Download audio clips from GitHub
    # Try zip first, fall back to individual files
    local_audio = Path("audio")
    local_audio.mkdir(exist_ok=True)
    for f in local_audio.glob("*.mp3"):
        f.unlink()

    zip_path = f"audio/{date_str}/clips.zip"
    logger.info(f"\nTrying zip: {zip_path}")
    zip_data = download_from_github(zip_path)

    if zip_data:
        import zipfile
        import io
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            zf.extractall(str(local_audio))
        downloaded = len(list(local_audio.glob("*.mp3")))
        logger.info(f"Extracted {downloaded} clips from zip")
    else:
        logger.info("No zip found. Downloading individual clips...")
        clip_list = list_github_folder(f"audio/{date_str}")
        logger.info(f"Found {len(clip_list)} clips to download")
        if not clip_list:
            send_status_email(f"FRICTION FAILED: No Clips | {date_str}",
                f"No audio clips found at audio/{date_str}/\nMake sure Step 2 ran first.")
            sys.exit(1)
        downloaded = 0
        for clip_path in clip_list:
            clip_name = clip_path.split("/")[-1]
            data = download_from_github(clip_path)
            if data:
                with open(local_audio / clip_name, "wb") as f:
                    f.write(data)
                downloaded += 1
                if downloaded % 20 == 0:
                    logger.info(f"  Downloaded {downloaded}/{len(clip_list)}")
        logger.info(f"Downloaded {downloaded} clips")

    # Build audio map
    audio_map = {}
    for f in sorted(local_audio.glob("*.mp3")):
        parts = f.stem.split("_", 1)
        if len(parts) == 2:
            try:
                audio_map[int(parts[0])] = f
            except ValueError:
                pass
    logger.info(f"Mapped {len(audio_map)} clips to lines")

    # Load music
    music_dir = Path("music")
    music = {}
    for name in ("intro", "transition", "outro"):
        mp = music_dir / f"{name}.mp3"
        if mp.exists():
            try:
                music[name] = AudioSegment.from_mp3(str(mp))
                logger.info(f"Loaded music: {name}.mp3 ({len(music[name])/1000:.1f}s)")
            except Exception as e:
                logger.warning(f"Could not load {name}.mp3: {e}")
        else:
            logger.info(f"Music not found: {mp}")

    # Assemble episode
    logger.info("\nAssembling episode...")
    episode = AudioSegment.empty()

    if "intro" in music:
        episode += music["intro"]
        episode += AudioSegment.silent(duration=300)

    current_segment = ""
    previous_character = ""
    INTERRUPTION_STARTERS = ["hold on", "wait", "no no", "oh come on", "can i",
                             "let me", "hang on", "stop", "whoa"]
    QUICK_REACTIONS = ["ha!", "wow", "oh man", "pfft", "yeesh", "ohhh", "classic",
                       "oh no", "yep", "nope", "right", "exactly"]

    clips_used = 0
    for line in lines:
        try:
            line_num = int(line.get("line") or 0)
            character = str(line.get("character") or "")
            segment = str(line.get("segment") or "")
            text = str(line.get("text") or "").lower().strip()
            direction = str(line.get("direction") or "").lower().strip()
        except Exception:
            continue

        if segment != current_segment and current_segment != "":
            if "transition" in music:
                episode += AudioSegment.silent(duration=200)
                episode += music["transition"]
                episode += AudioSegment.silent(duration=200)
            else:
                episode += AudioSegment.silent(duration=random.randint(700, 1100))
            logger.info(f"  --- {segment.upper()} ---")
            current_segment = segment
        elif current_segment == "":
            current_segment = segment
            logger.info(f"  --- {segment.upper()} ---")

        if line_num in audio_map:
            try:
                clip = AudioSegment.from_mp3(str(audio_map[line_num]))
                if previous_character == character:
                    pause = random.randint(80, 180)
                elif "interrupt" in direction or any(text.startswith(s) for s in INTERRUPTION_STARTERS):
                    pause = random.randint(0, 50)
                elif any(text.startswith(r) for r in QUICK_REACTIONS) or len(text.split()) <= 3:
                    pause = random.randint(30, 120)
                else:
                    pause = random.randint(200, 400)
                episode += AudioSegment.silent(duration=pause)
                episode += clip
                previous_character = character
                clips_used += 1
            except Exception as e:
                logger.warning(f"Error loading clip {line_num}: {e}")

    if "outro" in music:
        episode += AudioSegment.silent(duration=500)
        episode += music["outro"]

    # Normalize
    target_dbfs = -16.0
    if episode.dBFS != float('-inf'):
        episode = episode.apply_gain(target_dbfs - episode.dBFS)

    episode = AudioSegment.silent(duration=500) + episode + AudioSegment.silent(duration=1000)

    # Export
    output_file = f"friction_{date_str}.mp3"
    episode.export(output_file, format="mp3", bitrate="192k",
        tags={"title": f"The Friction - {datetime.now().strftime('%B %d, %Y')}",
              "artist": "The Friction", "album": "The Friction Daily", "genre": "Podcast"})

    duration_min = len(episode) / 1000 / 60
    file_size_mb = os.path.getsize(output_file) / 1024 / 1024
    logger.info(f"\nEpisode: {duration_min:.1f} min, {file_size_mb:.1f} MB, {clips_used} clips")

    # Email episode as attachment
    logger.info("Emailing episode...")
    summary = f"""THE FRICTION - Episode Ready!
Date: {date_str}
Duration: {duration_min:.1f} minutes
File size: {file_size_mb:.1f} MB
Clips: {clips_used}/{len(lines)}

Episode attached to this email.
"""
    email_sent = send_episode_email(
        f"FRICTION EPISODE | {date_str} | {duration_min:.1f} min",
        summary, output_file)

    if not email_sent:
        send_status_email(f"FRICTION WARNING: Episode built but email failed | {date_str}",
            f"Episode was mixed ({duration_min:.1f} min) but could not be emailed.\nFile size: {file_size_mb:.1f} MB")

    logger.info("\nStep 3 complete.")

if __name__ == "__main__":
    main()
