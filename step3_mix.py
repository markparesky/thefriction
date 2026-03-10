# THE FRICTION - Step 3: Mix
# Downloads audio clips from Dropbox, mixes with music, saves episode to Dropbox

import os
import json
import logging
import sys
import random
import requests
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("friction.mix")

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

def save_to_dropbox(data_bytes, path):
    if not DROPBOX_TOKEN:
        return False
    try:
        resp = requests.post("https://content.dropboxapi.com/2/files/upload",
            headers={"Authorization": f"Bearer {DROPBOX_TOKEN}",
                     "Dropbox-API-Arg": json.dumps({"path": path, "mode": "overwrite", "autorename": False}),
                     "Content-Type": "application/octet-stream"},
            data=data_bytes, timeout=300)
        if resp.status_code == 200:
            logger.info(f"Saved to Dropbox: {path}")
            return True
        else:
            logger.error(f"Dropbox save failed: {resp.status_code} - {resp.text[:300]}")
            return False
    except Exception as e:
        logger.error(f"Dropbox error: {e}")
        return False

def download_from_dropbox(path):
    if not DROPBOX_TOKEN:
        return None
    try:
        resp = requests.post("https://content.dropboxapi.com/2/files/download",
            headers={"Authorization": f"Bearer {DROPBOX_TOKEN}",
                     "Dropbox-API-Arg": json.dumps({"path": path})},
            timeout=120)
        if resp.status_code == 200:
            return resp.content
        else:
            logger.error(f"Download failed: {resp.status_code}")
            return None
    except Exception as e:
        logger.error(f"Download error: {e}")
        return None

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
        logger.error(f"Link error: {e}")
    return ""

def list_dropbox_folder(path):
    if not DROPBOX_TOKEN:
        return []
    try:
        resp = requests.post("https://api.dropboxapi.com/2/files/list_folder",
            headers={"Authorization": f"Bearer {DROPBOX_TOKEN}", "Content-Type": "application/json"},
            json={"path": path}, timeout=30)
        if resp.status_code == 200:
            entries = resp.json().get("entries", [])
            return [e.get("path_lower", "") for e in entries if e.get("name", "").endswith(".mp3")]
        else:
            logger.error(f"List folder failed: {resp.status_code} - {resp.text[:200]}")
            return []
    except Exception as e:
        logger.error(f"List folder error: {e}")
        return []

def main():
    logger.info("=" * 60)
    logger.info("THE FRICTION - Step 3: Mix")
    logger.info(f"Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 60)

    from pydub import AudioSegment

    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    # Download script from Dropbox
    script_path = f"/scripts/friction_{date_str}.json"
    logger.info(f"\nDownloading script: {script_path}")
    script_data = download_from_dropbox(script_path)
    if not script_data:
        send_status_email(f"FRICTION FAILED: No Script | {date_str}",
            f"Could not find script at {script_path}")
        sys.exit(1)

    script = json.loads(script_data.decode("utf-8"))
    lines = script.get("script", [])
    logger.info(f"Script: {len(lines)} lines")

    # List audio clips from Dropbox
    audio_folder = f"/audio/{date_str}"
    logger.info(f"\nListing audio clips: {audio_folder}")
    clip_paths = list_dropbox_folder(audio_folder)
    logger.info(f"Found {len(clip_paths)} clips")

    if not clip_paths:
        send_status_email(f"FRICTION FAILED: No Audio Clips | {date_str}",
            f"No audio clips found at {audio_folder}\nMake sure Step 2 ran first.")
        sys.exit(1)

    # Download all clips to local temp directory
    local_audio = Path("audio")
    local_audio.mkdir(exist_ok=True)
    for f in local_audio.glob("*.mp3"):
        f.unlink()

    logger.info("Downloading clips...")
    downloaded = 0
    for clip_path in clip_paths:
        filename = clip_path.split("/")[-1]
        data = download_from_dropbox(clip_path)
        if data:
            with open(local_audio / filename, "wb") as f:
                f.write(data)
            downloaded += 1
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
            logger.info(f"Music not found (optional): {mp}")

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

        # Segment transition
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

        # Add clip
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

    # Outro
    if "outro" in music:
        episode += AudioSegment.silent(duration=500)
        episode += music["outro"]

    # Normalize
    target_dbfs = -16.0
    if episode.dBFS != float('-inf'):
        episode = episode.apply_gain(target_dbfs - episode.dBFS)

    episode = AudioSegment.silent(duration=500) + episode + AudioSegment.silent(duration=1000)

    # Export
    output_path = "episode.mp3"
    episode.export(output_path, format="mp3", bitrate="192k",
        tags={"title": f"The Friction - {datetime.now().strftime('%B %d, %Y')}",
              "artist": "The Friction", "album": "The Friction Daily", "genre": "Podcast"})

    duration_min = len(episode) / 1000 / 60
    file_size_mb = os.path.getsize(output_path) / 1024 / 1024
    logger.info(f"\nEpisode: {duration_min:.1f} min, {file_size_mb:.1f} MB, {clips_used} clips")

    # Upload episode to Dropbox
    logger.info("Uploading episode to Dropbox...")
    dropbox_ep_path = f"/episodes/friction_{date_str}.mp3"
    with open(output_path, "rb") as f:
        saved = save_to_dropbox(f.read(), dropbox_ep_path)

    episode_link = get_dropbox_link(dropbox_ep_path) if saved else ""

    # Email
    summary = f"""THE FRICTION - Episode Ready!
Date: {date_str}
Duration: {duration_min:.1f} minutes
File size: {file_size_mb:.1f} MB
Clips used: {clips_used}/{len(lines)}

LISTEN: {episode_link or 'upload failed'}
"""
    send_status_email(f"FRICTION EPISODE READY | {date_str} | {duration_min:.1f} min", summary)
    logger.info("\nStep 3 complete.")

if __name__ == "__main__":
    main()
