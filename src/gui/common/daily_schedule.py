from collections.abc import Callable
from datetime import datetime, time, timedelta


class DailySchedule:
    """Track the next local occurrence and defer it while another task is busy."""

    def __init__(self):
        self._due: datetime | None = None

    def configure(self, now: datetime, start_time: time | None):
        self._due = None
        if start_time is None:
            return
        due = datetime.combine(now.date(), start_time)
        # Opening WWA during the scheduled minute still counts as being on time.
        self._due = due if now < due + timedelta(minutes=1) else due + timedelta(days=1)

    def poll(self, now: datetime, last_date: str, submit: Callable[[], bool]) -> str | None:
        due = self._due
        if due is None or now < due:
            return None
        if due.date() < now.date():
            # After sleep or a long delay, consider today's occurrence only.
            due = self._due = datetime.combine(now.date(), due.time())
            if now < due:
                return None

        date = due.date().isoformat()
        if date <= last_date:
            self._due = due + timedelta(days=1)
            return None
        if not submit():
            return None
        self._due = due + timedelta(days=1)
        return date
