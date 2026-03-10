# THE FRICTION - Step 2: Speak
# Reads script from Dropbox, generates audio clips, saves clips to Dropbox

import os
import json
import logging
import sys
import requests
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("friction.speak")

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
DROPBOX_TOKEN = os.getenv("DROPBOX_TOKEN", "")
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
                  "subject": subject, "html": f"<pre style='font-family:monospace;font-size:14px;'>{body_text}</pre>"})
    except Exception as e:
        logger.error(f"Email failed: {e}")

def save_to_dropbox(data_bytes, path):
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
            return True
        elif resp.status_code == 401:
            send_status_email("FRICTION FAILED: Dropbox Token Expired",
                "Your Dropbox token is invalid. Regenerate it at dropbox.com/developers/apps")
            return False
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
            timeout=60)
        if resp.status_code == 200:
            return resp.content
        else:
            logger.error(f"Dropbox download failed: {resp.status_code} - {resp.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"Dropbox download error: {e}")
        return None

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
            send_status_email("FRICTION FAILED: ElevenLabs API Key Invalid",
                "Check your ElevenLabs API key in Railway variables.")
            sys.exit(1)
        elif resp.status_code == 429:
            logger.error("ELEVENLABS RATE LIMITED OR OUT OF CREDITS")
            send_status_email("FRICTION FAILED: ElevenLabs Out of Credits",
                f"You may be out of ElevenLabs credits or rate limited.\n\n{resp.text[:300]}")
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
    script_path = f"/scripts/friction_{date_str}.json"

    # Download script from Dropbox
    logger.info(f"\nDownloading script: {script_path}")
    script_data = download_from_dropbox(script_path)
    if not script_data:
        send_status_email(f"FRICTION FAILED: No Script | {date_str}",
            f"Could not find script at {script_path}\nMake sure Step 1 ran first.")
        sys.exit(1)

    script = json.loads(script_data.decode("utf-8"))
    lines = script.get("script", [])
    logger.info(f"Script: {len(lines)} lines")

    if not ELEVENLABS_API_KEY:
        send_status_email(f"FRICTION FAILED: No ElevenLabs Key | {date_str}",
            "ELEVENLABS_API_KEY not set in Railway variables.")
        sys.exit(1)

    # Synthesize each line
    success = 0
    failed = 0
    total = len(lines)
    clips_saved = 0

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

        # Adjust settings based on direction
        settings = dict(VOICE_SETTINGS.get(character, VOICE_SETTINGS["LEO"]))
        if any(d in direction for d in ["laughing", "amused"]):
            settings["style"] = min(1.0, settings["style"] + 0.3)
        elif any(d in direction for d in ["angry", "heated"]):
            settings["style"] = min(1.0, settings["style"] + 0.2)
        elif any(d in direction for d in ["sarcastic", "deadpan"]):
            settings["stability"] = min(1.0, settings["stability"] + 0.15)

        audio = synthesize_line(text, character, settings)
        if audio:
            clip_path = f"/audio/{date_str}/{line_num:04d}_{character}.mp3"
            if save_to_dropbox(audio, clip_path):
                clips_saved += 1
            success += 1
        else:
            failed += 1

    logger.info(f"\nSynthesis complete:")
    logger.info(f"  Successful: {success}/{total}")
    logger.info(f"  Failed: {failed}/{total}")
    logger.info(f"  Clips saved to Dropbox: {clips_saved}")

    if success == 0:
        send_status_email(f"FRICTION FAILED: No Audio | {date_str}",
            "Zero lines synthesized. Check ElevenLabs API key and credits.")
        sys.exit(1)

    # Save manifest
    manifest = {"date": date_str, "total_lines": total, "success": success,
                "failed": failed, "clips_saved": clips_saved,
                "audio_folder": f"/audio/{date_str}"}
    save_to_dropbox(json.dumps(manifest, indent=2).encode("utf-8"),
                    f"/audio/{date_str}/manifest.json")

    summary = f"""THE FRICTION - Voice Synthesis Complete
Date: {date_str}
Lines: {success}/{total} synthesized
Failed: {failed}
Clips: /audio/{date_str}/

Ready for Step 3 (Mix).
"""
    send_status_email(f"FRICTION Voices Done | {date_str} | {success}/{total}", summary)
    logger.info("\nStep 2 complete.")

if __name__ == "__main__":
    main()
