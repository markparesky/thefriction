# THE FRICTION - Step 2: Speak
# Reads script from GitHub, generates audio clips, saves clips to GitHub

import os
import json
import logging
import sys
import base64
import requests
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("friction.speak")

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = "markparesky/thefriction"
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "")

VOICE_MAP = {
    "LEO": os.getenv("VOICE_ID_LEO", ""),
    "PRINGLE": os.getenv("VOICE_ID_PRINGLE", ""),
    "BREE": os.getenv("VOICE_ID_BREE", ""),
    "DUKE": os.getenv("VOICE_ID_DUKE", ""),
    "JAX": os.getenv("VOICE_ID_JAX", ""),
}

VOICE_SETTINGS = {
    "LEO": {"stability": 0.65, "similarity_boost": 0.75, "style": 0.35},
    "PRINGLE": {"stability": 0.75, "similarity_boost": 0.75, "style": 0.25},
    "BREE": {"stability": 0.45, "similarity_boost": 0.75, "style": 0.55},
    "DUKE": {"stability": 0.65, "similarity_boost": 0.75, "style": 0.25},
    "JAX": {"stability": 0.35, "similarity_boost": 0.65, "style": 0.65},
}

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

def download_from_github(filepath):
    if not GITHUB_TOKEN:
        return None
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filepath}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            return base64.b64decode(resp.json().get("content", ""))
        else:
            logger.error(f"GitHub download failed ({filepath}): {resp.status_code}")
            return None
    except Exception as e:
        logger.error(f"GitHub download error: {e}")
        return None

def save_to_github(content_bytes, filepath, message):
    if not GITHUB_TOKEN:
        return False
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filepath}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    content_b64 = base64.b64encode(content_bytes).decode("utf-8")
    sha = None
    try:
        check = requests.get(url, headers=headers, timeout=30)
        if check.status_code == 200:
            sha = check.json().get("sha")
    except Exception:
        pass
    payload = {"message": message, "content": content_b64}
    if sha:
        payload["sha"] = sha
    try:
        resp = requests.put(url, headers=headers, json=payload, timeout=120)
        if resp.status_code in (200, 201):
            return True
        else:
            logger.error(f"GitHub save failed ({filepath}): {resp.status_code} - {resp.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"GitHub save error: {e}")
        return False

def synthesize_line(text, character, settings):
    voice_id = VOICE_MAP.get(character, "")
    if not voice_id:
        return None
    try:
        resp = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={"Accept": "audio/mpeg", "Content-Type": "application/json",
                     "xi-api-key": ELEVENLABS_API_KEY},
            json={"text": text, "model_id": "eleven_multilingual_v2",
                  "voice_settings": {"stability": settings["stability"],
                                     "similarity_boost": settings["similarity_boost"],
                                     "style": settings["style"], "use_speaker_boost": True}},
            timeout=60)
        if resp.status_code == 200:
            return resp.content
        elif resp.status_code == 401:
            logger.error("ELEVENLABS API KEY INVALID")
            send_status_email("FRICTION FAILED: ElevenLabs Key Invalid",
                "Check ELEVENLABS_API_KEY in Railway.")
            sys.exit(1)
        elif resp.status_code == 429:
            logger.error("ELEVENLABS OUT OF CREDITS")
            send_status_email("FRICTION FAILED: ElevenLabs Credits",
                f"Out of credits or rate limited.\n\n{resp.text[:300]}")
            sys.exit(1)
        else:
            logger.warning(f"ElevenLabs error {resp.status_code}: {resp.text[:200]}")
            return None
    except Exception as e:
        logger.warning(f"Synthesis error: {e}")
        return None

def main():
    logger.info("=" * 60)
    logger.info("THE FRICTION - Step 2: Speak")
    logger.info(f"Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 60)

    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    filename = f"friction_{date_str}.json"

    # Download script from GitHub
    logger.info(f"\nDownloading script: scripts/{filename}")
    script_data = download_from_github(f"scripts/{filename}")
    if not script_data:
        send_status_email(f"FRICTION FAILED: No Script | {date_str}",
            f"Could not find scripts/{filename}\nMake sure Step 1 ran first.")
        sys.exit(1)

    script = json.loads(script_data.decode("utf-8"))
    lines = script.get("script", [])
    logger.info(f"Script: {len(lines)} lines")

    if not ELEVENLABS_API_KEY:
        send_status_email(f"FRICTION FAILED: No ElevenLabs Key | {date_str}",
            "ELEVENLABS_API_KEY not set.")
        sys.exit(1)

    success = 0
    failed = 0
    total = len(lines)

    local_audio = Path("audio")
    local_audio.mkdir(exist_ok=True)
    for f in local_audio.glob("*.mp3"):
        f.unlink()

    logger.info(f"\nSynthesizing {total} lines...")
    for i, line in enumerate(lines):
        line_num = int(line.get("line") or i + 1)
        character = str(line.get("character") or "LEO")
        text = str(line.get("text") or "")
        direction = str(line.get("direction") or "").lower()

        if not text.strip():
            continue

        if i % 10 == 0:
            logger.info(f"  Progress: {i}/{total} ({i*100//total}%)")

        settings = dict(VOICE_SETTINGS.get(character, VOICE_SETTINGS["LEO"]))
        if any(d in direction for d in ["laughing", "amused"]):
            settings["style"] = min(1.0, settings["style"] + 0.3)
        elif any(d in direction for d in ["angry", "heated"]):
            settings["style"] = min(1.0, settings["style"] + 0.2)
        elif any(d in direction for d in ["sarcastic", "deadpan"]):
            settings["stability"] = min(1.0, settings["stability"] + 0.15)

        audio = synthesize_line(text, character, settings)
        if audio:
            clip_name = f"{line_num:04d}_{character}.mp3"
            local_path = local_audio / clip_name
            with open(local_path, "wb") as f:
                f.write(audio)
            success += 1
        else:
            failed += 1

    logger.info(f"\nSynthesis complete:")
    logger.info(f"  Successful: {success}/{total}")
    logger.info(f"  Failed: {failed}/{total}")

    if success == 0:
        send_status_email(f"FRICTION FAILED: No Audio | {date_str}",
            "Zero lines synthesized. Check ElevenLabs key and credits.")
        sys.exit(1)

    # Zip all clips and upload to GitHub as one file
    import zipfile
    zip_path = f"audio_{date_str}.zip"
    logger.info(f"\nZipping {success} clips...")
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for mp3 in sorted(local_audio.glob("*.mp3")):
            zf.write(mp3, mp3.name)
    zip_size_mb = os.path.getsize(zip_path) / 1024 / 1024
    logger.info(f"Zip file: {zip_size_mb:.1f} MB")

    with open(zip_path, "rb") as f:
        zip_data = f.read()
    github_zip_path = f"audio/{date_str}/clips.zip"
    logger.info(f"Uploading to GitHub: {github_zip_path}")
    saved = save_to_github(zip_data, github_zip_path, f"Audio clips: {date_str}")

    summary = f"""THE FRICTION - Voice Synthesis Complete
Date: {date_str}
Lines: {success}/{total} synthesized
Failed: {failed}
Zip size: {zip_size_mb:.1f} MB
Saved to GitHub: {'YES' if saved else 'FAILED'}

Ready for Step 3 (Mix).
"""
    send_status_email(f"FRICTION Voices Done | {date_str} | {success}/{total}", summary)
    logger.info("\nStep 2 complete.")

if __name__ == "__main__":
    main()
