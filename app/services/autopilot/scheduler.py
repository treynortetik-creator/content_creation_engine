"""Background scheduler for autopilot monitoring."""
import asyncio
from datetime import datetime
from typing import Optional

from app.services.autopilot.monitor import run_all_due_monitors


class AutopilotScheduler:
    """
    Background scheduler that periodically checks all due monitors.

    Runs as a background task and checks monitors at a configured interval.
    """

    def __init__(self, check_interval: int = 300):
        """
        Initialize the scheduler.

        Args:
            check_interval: How often to check for due monitors (in seconds)
        """
        self.running = False
        self.check_interval = check_interval  # Default: check every 5 minutes
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        """Start the background scheduler."""
        if self.running:
            print("[Autopilot] Scheduler already running")
            return

        self.running = True
        print(f"[Autopilot] Scheduler started at {datetime.now()}")
        print(f"[Autopilot] Checking for due monitors every {self.check_interval} seconds")

        while self.running:
            try:
                results = await run_all_due_monitors()

                for result in results:
                    if result["status"] == "success":
                        print(
                            f"[Autopilot] {result['monitor']}: "
                            f"Found {result['new_items']} new items, "
                            f"created {result['jobs_created']} jobs"
                        )
                    elif result["status"] == "error":
                        print(
                            f"[Autopilot] {result['monitor']}: "
                            f"Error - {result['error']}"
                        )
                    elif result["status"] == "no_new_content":
                        # Only log periodically to avoid spam
                        pass

            except Exception as e:
                print(f"[Autopilot] Scheduler error: {e}")

            # Wait before next check
            await asyncio.sleep(self.check_interval)

    def stop(self):
        """Stop the scheduler."""
        self.running = False
        if self._task:
            self._task.cancel()
        print("[Autopilot] Scheduler stopped")

    async def run_once(self) -> list[dict]:
        """
        Run a single check cycle.

        Useful for testing or manual triggers.

        Returns:
            List of results from checked monitors
        """
        return await run_all_due_monitors()


# Global scheduler instance
scheduler = AutopilotScheduler()


async def start_scheduler():
    """Start the global scheduler."""
    await scheduler.start()


def stop_scheduler():
    """Stop the global scheduler."""
    scheduler.stop()
