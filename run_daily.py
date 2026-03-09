"""
THE FRICTION - Master Pipeline Orchestrator
This is the script Railway runs on schedule.
For now, it runs the news ingestion step.
We'll add more steps as we build them.
"""

import sys
import logging
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("friction.pipeline")

def main():
    logger.info("=" * 60)
    logger.info("THE FRICTION - Daily Pipeline")
    logger.info(f"Run time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 60)

    # --- PHASE 1: News Ingestion ---
    logger.info("\nStarting Phase 1: News Ingestion...")
    try:
        import news_ingestion
        news_ingestion.run()
        logger.info("Phase 1 complete.")
    except Exception as e:
        logger.error(f"Phase 1 FAILED: {e}")
        sys.exit(1)

    # --- PHASE 2: Script Generation ---
    logger.info("\nStarting Phase 2: Script Generation...")
    try:
        import generate_script
        generate_script.run()
        logger.info("Phase 2 complete.")
    except Exception as e:
        logger.error(f"Phase 2 FAILED: {e}")
        sys.exit(1)

    # --- STORY MEMORY UPDATE ---
    logger.info("\nUpdating story memory...")
    try:
        import story_memory
        story_memory.run()
        logger.info("Story memory updated.")
    except Exception as e:
        logger.warning(f"Story memory update failed (non-fatal): {e}")

    # --- PHASE 3: Voice Synthesis ---
    logger.info("\nStarting Phase 3: Voice Synthesis...")
    try:
        import synthesize_voices
        synthesize_voices.run()
        logger.info("Phase 3 complete.")
    except Exception as e:
        logger.error(f"Phase 3 FAILED: {e}")
        sys.exit(1)

    # --- PHASE 4: Audio Mixing ---
    logger.info("\nStarting Phase 4: Audio Mixing...")
    try:
        import mix_episode
        mix_episode.run()
        logger.info("Phase 4 complete.")
    except Exception as e:
        logger.error(f"Phase 4 FAILED: {e}")
        sys.exit(1)

    # --- PHASE 5: Upload ---
    # (Coming next)

    # --- EMAIL NOTIFICATION ---
    logger.info("\nSending email notification...")
    try:
        import send_notification
        send_notification.run()
        logger.info("Email sent.")
    except Exception as e:
        logger.warning(f"Email notification failed (non-fatal): {e}")

    logger.info("\n" + "=" * 60)
    logger.info("Pipeline complete.")
    logger.info("=" * 60)

if __name__ == "__main__":
    main()
