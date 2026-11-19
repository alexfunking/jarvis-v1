"""The Decision Inbox: job-finder matches the user has not answered yet, scored by job-finder itself."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from jobfinder.labels import load_labels
from jobfinder.matches import match_rows
from jobfinder.scoring import Verdict, load_priorities
from jobfinder.storage import LATER, JobStore

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
    """Open matches with no label and no answer other than "later", best first.

    Reads the same database, labels and priorities the daily run uses; nothing is written.
    """
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
        if answers.get(job.key, LATER) != LATER:
            continue
        items.append(InboxItem(job.key, job.title, job.company, job.location, row.score.points, job.url))
        if len(items) >= limit:
            break
    return items


def to_json(items: list[InboxItem]) -> list[dict[str, object]]:
    return [asdict(item) for item in items]
