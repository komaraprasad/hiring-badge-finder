"""Plain-words summary text for a job (shown on the dashboard)."""
from __future__ import annotations


def summary_line(job: dict) -> str:
    parts = [
        f"{job['not_hiring']} not hiring",
        f"{job['other_locations']} other locations",
        f"{job['already_collected']} already collected",
        f"{job['duplicates']} duplicates",
    ]
    if job.get("not_confirmed"):
        parts.insert(1, f"{job['not_confirmed']} without the badge")
    if job.get("old_roles"):
        parts.insert(1, f"{job['old_roles']} with only older open roles")
    pages = job["pages"]
    if job.get("skill_dropped"):
        parts.insert(0, f"{job['skill_dropped']} not hiring for the skill (removed)")
    return (f"{job['saved']} of {job['target']} saved · read {job['total_read']} people on "
            f"{pages} page{'s' if pages != 1 else ''} · filtered out: " + ", ".join(parts))


def short_reason(job: dict) -> str:
    """Why the run ended short of the target. Empty when it did not."""
    if job["status"] == "running" or job["saved"] >= job["target"]:
        return ""
    reason = job.get("end_reason") or ""
    saved = job["saved"]
    if job["status"] == "stopped":
        return f"Stopped. {saved} saved so far."
    if job["status"] == "error":
        return job.get("error") or "The run stopped because of an error."
    if reason == "no more pages":
        return (f"Only {saved} new people for these filters. "
                "Add more roles or locations to find more.")
    if reason == "page limit reached":
        return (f"Reached the limit of 30 result pages. {saved} people saved. "
                "Start another search with different roles or locations.")
    if reason == "daily limit reached":
        return (f"Reached today's reading limit for this browser. {saved} people saved. "
                "Try again tomorrow, or raise the limit in Settings.")
    if reason:
        return f"Ended early: {reason}. {saved} people saved."
    return ""
