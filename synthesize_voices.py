"""
THE FRICTION — Voice Synthesis (Phase 3)
==========================================
Takes daily_script.json and calls the ElevenLabs API to generate
audio for each line of dialogue using the correct character voice.

Output: Individual WAV/MP3 files in the audio/ directory

Requirements:
  - ELEVENLABS_API_KEY environment variable
  - VOICE_ID_LEO, VOICE_ID_PRINGLE, VOICE_ID_BREE, VOICE_ID_DUKE, VOICE_ID_JAX
  - daily_script.json from Phase 2
"""

import os
import json
import logging
import time
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("friction.voices")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
SCRIPT_INPUT_FILE = os.getenv("SCRIPT_OUTPUT_FILE", "daily_script.json")
AUDIO_OUTPUT_DIR = os.getenv("AUDIO_OUTPUT_DIR", "audio")

# Voice ID mapping
VOICE_MAP = {
    "LEO": os.getenv("VOICE_ID_LEO", ""),
    "PRINGLE": os.getenv("VOICE_ID_PRINGLE", ""),
    "BREE": os.getenv("VOICE_ID_BREE", ""),
    "DUKE": os.getenv("VOICE_ID_DUKE", ""),
    "JAX": os.getenv("VOICE_ID_JAX", ""),
}

# ElevenLabs API settings
ELEVENLABS_BASE_URL = "https://api.elevenlabs.io/v1"
MODEL_ID = "eleven_multilingual_v2"  # Best quality model

# Voice settings per character (stability, similarity_boost, style)
VOICE_SETTINGS = {
    "LEO": {"stability": 0.65, "similarity_boost": 0.75, "style": 0.35},
    "PRINGLE": {"stability": 0.75, "similarity_boost": 0.75, "style": 0.25},
    "BREE": {"stability": 0.45, "similarity_boost": 0.75, "style": 0.55},
    "DUKE": {"stability": 0.65, "similarity_boost": 0.75, "style": 0.25},
    "JAX": {"stability": 0.35, "similarity_boost": 0.65, "style": 0.65},
}

# Rate limiting
DELAY_BETWEEN_CALLS = 0.2  # seconds between API calls (reduced for speed)
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds to wait on rate limit


# ---------------------------------------------------------------------------
# Load Script
# ---------------------------------------------------------------------------

def load_script() -> dict:
    """Load the episode script from Phase 2."""
    path = Path(SCRIPT_INPUT_FILE)
    if not path.exists():
        logger.error(f"Script file not found: {SCRIPT_INPUT_FILE}")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        script = json.load(f)

    lines = script.get("script", [])
    logger.info(f"Loaded script with {len(lines)} lines")
    return script


# ---------------------------------------------------------------------------
# ElevenLabs API
# ---------------------------------------------------------------------------

def synthesize_line(text: str, character: str, line_num: int, direction: str = "") -> bytes | None:
    """
    Call ElevenLabs TTS API for a single line of dialogue.
    Returns the audio bytes (MP3) or None on failure.
    """
    voice_id = VOICE_MAP.get(character, "")
    if not voice_id:
        logger.error(f"No voice ID configured for character: {character}")
        return None

    settings = VOICE_SETTINGS.get(character, VOICE_SETTINGS["LEO"])

    url = f"{ELEVENLABS_BASE_URL}/text-to-speech/{voice_id}"

    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": ELEVENLABS_API_KEY,
    }

    # Add delivery direction to the text if provided
    # ElevenLabs responds to emotional cues in the text
    if direction:
        synth_text = f"({direction}) {text}"
    else:
        synth_text = text

    payload = {
        "text": synth_text,
        "model_id": MODEL_ID,
        "voice_settings": {
            "stability": settings["stability"],
            "similarity_boost": settings["similarity_boost"],
            "style": settings["style"],
            "use_speaker_boost": True,
        },
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=60)

            if response.status_code == 200:
                return response.content

            elif response.status_code == 429:
                # Rate limited
                retry_after = int(response.headers.get("Retry-After", RETRY_DELAY))
                logger.warning(f"  Rate limited on line {line_num}. Waiting {retry_after}s...")
                time.sleep(retry_after)

            elif response.status_code == 401:
                logger.error("ElevenLabs API key is invalid. Check ELEVENLABS_API_KEY.")
                sys.exit(1)

            elif response.status_code == 422:
                logger.warning(f"  Line {line_num}: Text validation error. Trying with cleaned text...")
                # Sometimes special characters cause issues; try plain text
                payload["text"] = text  # Remove direction prefix
                continue

            else:
                logger.error(f"  Line {line_num}: API returned {response.status_code}: {response.text[:200]}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)

        except requests.exceptions.Timeout:
            logger.warning(f"  Line {line_num}: Request timed out (attempt {attempt})")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

        except Exception as e:
            logger.error(f"  Line {line_num}: Error: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

    logger.error(f"  Line {line_num}: All {MAX_RETRIES} attempts failed.")
    return None


# ---------------------------------------------------------------------------
# Main Pipeline
# ---------------------------------------------------------------------------

def run():
    """Execute the voice synthesis pipeline."""
    logger.info("=" * 60)
    logger.info("THE FRICTION — Voice Synthesis (Phase 3)")
    logger.info(f"Run time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 60)

    # Validate configuration
    if not ELEVENLABS_API_KEY:
        logger.error("ELEVENLABS_API_KEY not set.")
        sys.exit(1)

    missing_voices = [char for char, vid in VOICE_MAP.items() if not vid]
    if missing_voices:
        logger.error(f"Missing voice IDs for: {missing_voices}")
        sys.exit(1)

    # Load script
    script = load_script()
    lines = script.get("script", [])

    # Create output directory
    audio_dir = Path(AUDIO_OUTPUT_DIR)
    audio_dir.mkdir(exist_ok=True)

    # Clear any existing audio files
    for f in audio_dir.glob("*.mp3"):
        f.unlink()

    # Synthesize each line
    total_lines = len(lines)
    success_count = 0
    fail_count = 0
    total_bytes = 0

    logger.info(f"\nSynthesizing {total_lines} lines of dialogue...")
    logger.info(f"Voices: {', '.join(f'{k}={v[:8]}...' for k, v in VOICE_MAP.items())}")
    logger.info("")

    for i, line in enumerate(lines):
        line_num = line.get("line", i + 1)
        character = line.get("character", "LEO")
        text = line.get("text", "")
        direction = line.get("direction", "")
        segment = line.get("segment", "")

        if not text.strip():
            logger.warning(f"  Line {line_num}: Empty text, skipping.")
            continue

        # Progress logging
        if i % 10 == 0:
            logger.info(f"  Progress: {i}/{total_lines} lines ({i/total_lines*100:.0f}%)")

        # Synthesize
        audio_data = synthesize_line(text, character, line_num, direction)

        if audio_data:
            # Save audio file
            filename = f"{line_num:04d}_{character}.mp3"
            filepath = audio_dir / filename
            with open(filepath, "wb") as f:
                f.write(audio_data)

            success_count += 1
            total_bytes += len(audio_data)
        else:
            fail_count += 1
            logger.warning(f"  Line {line_num} ({character}): FAILED — will be silent gap in episode.")

        # Rate limiting delay
        time.sleep(DELAY_BETWEEN_CALLS)

    # Summary
    logger.info("")
    logger.info("=" * 40)
    logger.info(f"Synthesis complete:")
    logger.info(f"  Successful: {success_count}/{total_lines}")
    logger.info(f"  Failed: {fail_count}/{total_lines}")
    logger.info(f"  Total audio: {total_bytes / 1024 / 1024:.1f} MB")
    logger.info(f"  Output directory: {audio_dir}")

    # Save a manifest for the mixing step
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_lines": total_lines,
        "success_count": success_count,
        "fail_count": fail_count,
        "audio_dir": str(audio_dir),
        "files": sorted([f.name for f in audio_dir.glob("*.mp3")]),
    }
    manifest_path = audio_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    logger.info(f"  Manifest: {manifest_path}")

    if fail_count > total_lines * 0.2:
        logger.error(f"Too many failures ({fail_count}/{total_lines}). Episode quality may be unacceptable.")
        sys.exit(1)

    logger.info("\nDone.")


if __name__ == "__main__":
    run()
