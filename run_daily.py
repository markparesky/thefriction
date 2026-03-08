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

    # --- PHASE 3: Voice Synthesis ---
    # (Coming soon)

    # --- PHASE 4: Mixing ---
    # (Coming soon)

    # --- PHASE 5: Upload ---
    # (Coming soon)

    logger.info("\n" + "=" * 60)
    logger.info("Pipeline complete.")
    logger.info("=" * 60)

if __name__ == "__main__":
    main()
