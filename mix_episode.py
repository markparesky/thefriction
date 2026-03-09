"""
THE FRICTION — Audio Mixing (Phase 4)
=======================================
Takes individual audio clips from Phase 3 and assembles them
into a finished podcast episode with pauses between speakers.

Output: episode.mp3

Requirements:
  - pydub (pip install pydub)
  - ffmpeg installed on the system
  - audio/ directory with MP3 files from Phase 3
  - daily_script.json for segment information
"""

import os
import json
import logging
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("friction.mixer")

AUDIO_INPUT_DIR = os.getenv("AUDIO_OUTPUT_DIR", "audio")
SCRIPT_INPUT_FILE = os.getenv("SCRIPT_OUTPUT_FILE", "daily_script.json")
EPISODE_OUTPUT_FILE = os.getenv("EPISODE_OUTPUT_FILE", "episode.mp3")


# ---------------------------------------------------------------------------
# Audio Assembly
# ---------------------------------------------------------------------------

def run():
    """Assemble audio clips into a finished episode."""
    logger.info("=" * 60)
    logger.info("THE FRICTION — Audio Mixing (Phase 4)")
    logger.info(f"Run time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 60)

    try:
        from pydub import AudioSegment
    except ImportError:
        logger.error("pydub not installed. Run: pip install pydub")
        sys.exit(1)

    audio_dir = Path(AUDIO_INPUT_DIR)
    if not audio_dir.exists():
        logger.error(f"Audio directory not found: {audio_dir}")
        sys.exit(1)

    # Load the script for segment info
    script_path = Path(SCRIPT_INPUT_FILE)
    if not script_path.exists():
        logger.error(f"Script file not found: {SCRIPT_INPUT_FILE}")
        sys.exit(1)

    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)

    lines = script.get("script", [])

    # Get sorted list of audio files
    audio_files = sorted(audio_dir.glob("*.mp3"))
    logger.info(f"Found {len(audio_files)} audio clips")

    if not audio_files:
        logger.error("No audio files found. Phase 3 may have failed.")
        sys.exit(1)

    # Build a map of line numbers to audio files
    audio_map = {}
    for f in audio_files:
        # Filename format: 0001_LEO.mp3
        parts = f.stem.split("_", 1)
        if len(parts) == 2:
            try:
                line_num = int(parts[0])
                audio_map[line_num] = f
            except ValueError:
                pass

    logger.info(f"Mapped {len(audio_map)} audio clips to script lines")

    # Assemble the episode
    logger.info("\nAssembling episode...")
    episode = AudioSegment.empty()

    # Pause durations (milliseconds)
    PAUSE_SAME_SPEAKER = (100, 200)    # Short pause, same person continues
    PAUSE_NEW_SPEAKER = (250, 450)     # Pause when switching speakers
    PAUSE_SEGMENT_TRANSITION = (800, 1200)  # Pause between segments

    current_segment = ""
    previous_character = ""
    clips_added = 0
    clips_missing = 0

    for line in lines:
        line_num = line.get("line", 0)
        character = line.get("character", "")
        segment = line.get("segment", "")

        # Add segment transition pause
        if segment != current_segment and current_segment != "":
            pause_ms = random.randint(*PAUSE_SEGMENT_TRANSITION)
            episode += AudioSegment.silent(duration=pause_ms)
            logger.info(f"  --- {segment.upper()} --- (transition pause: {pause_ms}ms)")
            current_segment = segment
        elif current_segment == "":
            current_segment = segment
            logger.info(f"  --- {segment.upper()} ---")

        # Find the audio clip
        if line_num in audio_map:
            try:
                clip = AudioSegment.from_mp3(str(audio_map[line_num]))

                # Add appropriate pause before the clip
                if previous_character == character:
                    pause_ms = random.randint(*PAUSE_SAME_SPEAKER)
                else:
                    pause_ms = random.randint(*PAUSE_NEW_SPEAKER)

                episode += AudioSegment.silent(duration=pause_ms)
                episode += clip
                clips_added += 1
                previous_character = character

            except Exception as e:
                logger.warning(f"  Line {line_num}: Error loading clip: {e}")
                clips_missing += 1
        else:
            clips_missing += 1

    # Normalize audio levels
    logger.info("\nNormalizing audio levels...")

    # Target loudness for podcasts: -16 LUFS (approximate with dBFS)
    target_dbfs = -16.0
    current_dbfs = episode.dBFS
    if current_dbfs != float('-inf'):
        change_in_dbfs = target_dbfs - current_dbfs
        episode = episode.apply_gain(change_in_dbfs)
        logger.info(f"  Adjusted gain by {change_in_dbfs:.1f} dB (from {current_dbfs:.1f} to {target_dbfs:.1f})")

    # Add a short silence at start and end
    episode = AudioSegment.silent(duration=500) + episode + AudioSegment.silent(duration=1000)

    # Export
    logger.info(f"\nExporting episode...")
    episode.export(
        EPISODE_OUTPUT_FILE,
        format="mp3",
        bitrate="192k",
        tags={
            "title": f"The Friction - {datetime.now().strftime('%B %d, %Y')}",
            "artist": "The Friction",
            "album": "The Friction Daily",
            "genre": "Podcast",
        },
    )

    # Summary
    duration_seconds = len(episode) / 1000
    duration_minutes = duration_seconds / 60
    file_size_mb = os.path.getsize(EPISODE_OUTPUT_FILE) / 1024 / 1024

    logger.info("")
    logger.info("=" * 40)
    logger.info(f"Episode assembled:")
    logger.info(f"  Duration: {duration_minutes:.1f} minutes ({duration_seconds:.0f} seconds)")
    logger.info(f"  Clips used: {clips_added}")
    logger.info(f"  Clips missing: {clips_missing}")
    logger.info(f"  File size: {file_size_mb:.1f} MB")
    logger.info(f"  Output: {EPISODE_OUTPUT_FILE}")

    if duration_minutes < 8:
        logger.warning(f"Episode is short ({duration_minutes:.1f} min). Expected ~15 min.")
    elif duration_minutes > 22:
        logger.warning(f"Episode is long ({duration_minutes:.1f} min). Expected ~15 min.")
    else:
        logger.info(f"  Duration is within target range.")

    logger.info("\nDone.")


if __name__ == "__main__":
    run()
