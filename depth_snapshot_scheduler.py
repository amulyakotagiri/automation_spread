"""
Runs once (triggered by a single GitHub Actions cron a few minutes before
market open), then internally sleeps to each target snapshot time and
fires the existing fetch-and-write logic. This avoids depending on 15
separately-scheduled cron triggers, which GitHub Actions does not
guarantee to fire on the exact minute.
"""
import time
from datetime import datetime, timedelta
import pytz

# Import your existing snapshot logic as a callable function.
# If main() in your current script isn't already split out this way,
# wrap its body in a function called `take_snapshot()` that does one
# fetch-and-write cycle, then import it here.
from live_depth_snapshot import take_snapshot  # <- your existing script, refactored to expose this

IST = pytz.timezone("Asia/Kolkata")

# Same 15 timestamps from your schedule
TARGET_TIMES = [
    "09:15", "09:18", "09:30", "09:45",
    "10:00", "10:30", "11:00", "11:30",
    "12:00", "12:30", "13:00", "13:30",
    "14:00", "14:30", "15:00",
]


def next_target_datetimes(today: datetime) -> list[datetime]:
    """Build today's target datetimes, skipping any already in the past."""
    targets = []
    for t in TARGET_TIMES:
        hh, mm = map(int, t.split(":"))
        dt = today.replace(hour=hh, minute=mm, second=0, microsecond=0)
        targets.append(dt)
    return targets


def run_schedule():
    now = datetime.now(IST)
    print(f"[SCHEDULER] Started at {now.strftime('%Y-%m-%d %H:%M:%S %Z')}", flush=True)

    targets = next_target_datetimes(now)

    for target in targets:
        now = datetime.now(IST)
        if target < now:
            print(f"[SCHEDULER] Skipping {target.strftime('%H:%M')} — already past", flush=True)
            continue

        wait_seconds = (target - now).total_seconds()
        print(f"[SCHEDULER] Waiting {wait_seconds/60:.1f} min until {target.strftime('%H:%M')} snapshot...", flush=True)
        time.sleep(max(0, wait_seconds))

        print(f"[SCHEDULER] Firing snapshot for {target.strftime('%H:%M')}", flush=True)
        try:
            take_snapshot(scheduled_time=target.strftime("%H:%M"))
        except Exception as e:
            # One failed snapshot shouldn't kill the rest of the day's schedule
            print(f"[SCHEDULER] Snapshot at {target.strftime('%H:%M')} failed: {e}", flush=True)

    print(f"[SCHEDULER] All {len(targets)} snapshots attempted. Done.", flush=True)


if __name__ == "__main__":
    run_schedule()
