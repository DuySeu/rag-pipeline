from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

GOLDEN_PATH = Path("evaluation/datasets/golden.jsonl")


@dataclass(frozen=True)
class GoldenSample:
    question: str
    reference: str
    sources: tuple[str, ...]


# Read the golden Q&A set, one JSON object per non-empty line
def load_golden(path: Path = GOLDEN_PATH) -> list[GoldenSample]:
    samples: list[GoldenSample] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not row.get("question") or not row.get("reference") or not row.get("sources"):
            raise ValueError(f"{path}:{line_no} needs question, reference and sources")
        samples.append(
            GoldenSample(
                question=row["question"], reference=row["reference"], sources=tuple(row["sources"])
            )
        )
    return samples
