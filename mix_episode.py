# THE FRICTION - Audio Mixing (Phase 4)
# Takes individual audio clips from Phase 3 and assembles them
# into a finished podcast episode with pauses between speakers.

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
MUSIC_DIR = "music"


# ---------------------------------------------------------------------------
# Audio Assembly
# ---------------------------------------------------------------------------

def run():
    """Assemble audio clips into a finished episode."""
    logger.info("=" * 60)
    logger.info("THE FRICTION - Audio Mixing (Phase 4)")
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

    # Load music files
    import os as _os
    cwd = _os.getcwd()
    logger.info(f"Working directory: {cwd}")
    logger.info(f"Files in working directory: {_os.listdir(cwd)}")
    
    music_dir = Path(cwd) / MUSIC_DIR
    logger.info(f"Looking for music in: {music_dir}")
    if music_dir.exists():
        logger.info(f"Music directory contents: {_os.listdir(str(music_dir))}")
    else:
        logger.info(f"Music directory does not exist: {music_dir}")
        # Try alternate paths
        alt_paths = [Path("/app/music"), Path("/app") / MUSIC_DIR, Path(MUSIC_DIR)]
        for alt in alt_paths:
            if alt.exists():
                logger.info(f"Found music at alternate path: {alt}")
                music_dir = alt
                break

    music = {}

    for name in ("intro", "transition", "outro", "bed"):
        music_path = music_dir / f"{name}.mp3"
        if music_path.exists():
            try:
                music[name] = AudioSegment.from_mp3(str(music_path))
                logger.info(f"Loaded music: {name}.mp3 ({len(music[name])/1000:.1f}s)")
            except Exception as e:
                logger.warning(f"Could not load {name}.mp3: {e}")
        else:
            logger.info(f"Music file not found: {music_path}")

    # Assemble the episode
    logger.info("\nAssembling episode...")
    episode = AudioSegment.empty()

    # Add intro music if available
    if "intro" in music:
        logger.info("  Adding intro music...")
        episode += music["intro"]
        episode += AudioSegment.silent(duration=300)  # Brief pause after intro

    # Pause durations (milliseconds)
    PAUSE_SAME_SPEAKER = (80, 180)       # Short pause, same person continues
    PAUSE_NORMAL_SWITCH = (200, 400)     # Normal speaker change
    PAUSE_SEGMENT_TRANSITION = (700, 1100)  # Pause between segments

    # Interruption/reaction detection keywords
    INTERRUPTION_STARTERS = [
        "hold on", "wait", "no no", "but-", "that's-", "oh come on",
        "can I", "let me", "hang on", "stop", "whoa", "excuse me",
    ]
    QUICK_REACTIONS = [
        "ha!", "wow", "oh man", "pfft", "yeesh", "ohhh", "classic",
        "oh no", "yep", "nope", "right", "exactly", "wait what",
        "seriously", "oh god", "geez", "damn", "true", "fair",
        "called it", "there it is", "oh boy", "oof",
    ]

    # Overlay detection - these get layered on top of the previous clip
    OVERLAY_DIRECTIONS = ["laughing in background", "background laughter", "chuckling over"]
    OVERLAY_REACTIONS = ["ha!", "oh man", "pfft", "ohhh", "wow"]

    def is_overlay_candidate(text_lower, direction_lower, word_count):
        """Determine if this line should be overlaid on the previous clip."""
        if any(d in direction_lower for d in OVERLAY_DIRECTIONS):
            return True
        if "background" in direction_lower:
            return True
        # Very short reactions from a different character while someone is talking
        if word_count <= 2 and any(text_lower.startswith(r) for r in OVERLAY_REACTIONS):
            return True
        return False

    current_segment = ""
    previous_character = ""
    previous_clip_duration = 0  # Track how long the last clip was
    clips_added = 0
    clips_missing = 0
    brief_start_position = 0

    for i, line in enumerate(lines):
        try:
            line_num = int(line.get("line") or i + 1)
            character = str(line.get("character") or "UNKNOWN")
            segment = str(line.get("segment") or "unknown")
            raw_text = str(line.get("text") or "")
            raw_direction = str(line.get("direction") or "")
            text = raw_text.lower().strip()
            direction = raw_direction.lower().strip()
            word_count = len(text.split()) if text else 0
        except Exception as e:
            logger.warning(f"  Skipping malformed line {i}: {e} - data: {line}")
            continue

        # Add segment transition
        if segment != current_segment and current_segment != "":
            # If leaving the Brief segment, overlay the music bed
            if current_segment == "brief" and "bed" in music:
                brief_end_position = len(episode)
                brief_duration = brief_end_position - brief_start_position
                if brief_duration > 0:
                    bed = music["bed"]
                    loops_needed = (brief_duration // len(bed)) + 1
                    looped_bed = bed * loops_needed
                    looped_bed = looped_bed[:brief_duration]
                    looped_bed = looped_bed.fade_in(1000).fade_out(2000)
                    episode = episode.overlay(looped_bed, position=brief_start_position)
                    logger.info(f"  Overlaid music bed on Brief segment ({brief_duration/1000:.1f}s)")

            # Add transition sting or pause
            if "transition" in music:
                episode += AudioSegment.silent(duration=200)
                episode += music["transition"]
                episode += AudioSegment.silent(duration=200)
            else:
                pause_ms = random.randint(*PAUSE_SEGMENT_TRANSITION)
                episode += AudioSegment.silent(duration=pause_ms)

            logger.info(f"  --- {segment.upper()} ---")

            # Track Brief start
            if segment == "brief":
                brief_start_position = len(episode)

            current_segment = segment
            previous_clip_duration = 0
        elif current_segment == "":
            current_segment = segment
            if segment == "brief":
                brief_start_position = len(episode)
            logger.info(f"  --- {segment.upper()} ---")

        # Find the audio clip
        if line_num in audio_map:
            try:
                clip = AudioSegment.from_mp3(str(audio_map[line_num]))

                # Check if this should be overlaid on the previous clip
                if (previous_character != character and
                    previous_clip_duration > 1000 and
                    is_overlay_candidate(text, direction, word_count) and
                    len(episode) > 500):

                    # Overlay this reaction on top of the end of the previous clip
                    # Place it starting 60-80% into the previous clip
                    overlay_clip = clip - 6  # Reduce volume slightly so it sits behind
                    overlap_start = max(0, len(episode) - int(previous_clip_duration * random.uniform(0.2, 0.4)))

                    episode = episode.overlay(overlay_clip, position=overlap_start)
                    clips_added += 1
                    # Don't update previous_character - the main speaker is still talking

                else:
                    # Normal sequential placement with smart pauses

                    if previous_character == character:
                        pause_ms = random.randint(*PAUSE_SAME_SPEAKER)

                    elif "interrupt" in direction or any(text.startswith(s) for s in INTERRUPTION_STARTERS):
                        # Interruption - almost no gap
                        pause_ms = random.randint(0, 50)

                    elif any(text.startswith(r) for r in QUICK_REACTIONS) or word_count <= 3:
                        # Quick reaction - very short gap
                        pause_ms = random.randint(30, 120)

                    elif "laughing" in direction or "chuckling" in direction:
                        pause_ms = random.randint(50, 150)

                    else:
                        pause_ms = random.randint(*PAUSE_NORMAL_SWITCH)

                    episode += AudioSegment.silent(duration=pause_ms)
                    episode += clip
                    clips_added += 1
                    previous_character = character
                    previous_clip_duration = len(clip)

            except Exception as e:
                logger.warning(f"  Line {line_num}: Error loading clip: {e}")
                clips_missing += 1
        else:
            clips_missing += 1

    # Add outro music if available
    if "outro" in music:
        logger.info("  Adding outro music...")
        episode += AudioSegment.silent(duration=500)
        episode += music["outro"]

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
