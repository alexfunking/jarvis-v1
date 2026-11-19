"""The Decision Inbox: job-finder matches the user has not answered yet, scored by job-finder itself.

An answer here is the same as a press on the Telegram digest's buttons: it is saved in job-finder's
database, and Applied or Not relevant also becomes a label. Later hides the job until the next digest
sends it again.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from jobfinder.digest import ANSWER_NAMES, record_answer
from jobfinder.labels import load_labels
from jobfinder.matches import match_rows
from jobfinder.scoring import Verdict, load_priorities
from jobfinder.storage import JobStore

__all__ = ["ANSWER_NAMES", "InboxItem", "answer", "job_matches", "to_json"]

MAX_ITEMS = 8


@dataclass(frozen=True, slots=True)
class InboxItem:
    key: str
    title: str
    company: str
    location: str
    points: int
    url: str


def job_matches(jobfinder_dir: Path, limit: int = MAX_ITEMS) -> list[InboxItem]:
    """Matches with no label and no answer, best first. Reads the database the daily run uses."""
    db = jobfinder_dir / "data" / "jobs.db"
    if not db.exists():
        return []
    priorities = load_priorities(jobfinder_dir / "priorities.txt")
    labels = load_labels(jobfinder_dir / "data" / "labels.jsonl")
    store = JobStore.open(db)
    try:
        answers = store.digest_answers()
        rows = match_rows(store, priorities, labels)
    finally:
        store.close()
    items: list[InboxItem] = []
    for row in rows:
        job = row.stored.job
        if row.score.verdict != Verdict.MATCH or row.label is not None:
            continue
        if job.key in answers:
            continue
        items.append(InboxItem(job.key, job.title, job.company, job.location, row.score.points, job.url))
        if len(items) >= limit:
            break
    return items


def answer(jobfinder_dir: Path, key: str, name: str) -> bool:
    """Record the user's answer to a job; False when the job or the answer is unknown."""
    db = jobfinder_dir / "data" / "jobs.db"
    if not db.exists():
        return False
    store = JobStore.open(db)
    try:
        saved = record_answer(store, jobfinder_dir / "data" / "labels.jsonl", key, name, datetime.now(UTC), "Jarvis")
    finally:
        store.close()
    return saved is not None


def to_json(items: list[InboxItem]) -> list[dict[str, object]]:
    return [asdict(item) for item in items]
