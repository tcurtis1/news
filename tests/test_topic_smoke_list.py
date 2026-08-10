"""Ensure smoke-topics.txt stays useful for deploy matrix."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOPICS = ROOT / "scripts" / "smoke-topics.txt"


def _load_topics() -> list[str]:
    out: list[str] = []
    for line in TOPICS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def test_smoke_topics_file_exists_and_has_dozens():
    topics = _load_topics()
    assert len(topics) >= 30, f"want ≥30 popular topics, got {len(topics)}"
    # Core chips that recently broke empty for conservative MyNews
    for must in ("Politics", "Economy", "Iran", "Tesla", "Trump"):
        assert must in topics


def test_smoke_topics_unique():
    topics = _load_topics()
    assert len(topics) == len(set(topics))
