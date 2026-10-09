"""DeskPilot Evaluation Harness package."""

from deskpilot.evals.datasets import (
    BenchmarkExample,
    DownloadSimulatedFile,
    ensure_downloads_dataset,
    get_canonical_downloads_dataset,
)
from deskpilot.evals.evaluators import (
    evaluate_categorization,
    evaluate_hitl_compliance,
    evaluate_reasoning_quality,
    evaluate_safety,
)

__all__ = [
    "BenchmarkExample",
    "DownloadSimulatedFile",
    "ensure_downloads_dataset",
    "get_canonical_downloads_dataset",
    "evaluate_categorization",
    "evaluate_hitl_compliance",
    "evaluate_reasoning_quality",
    "evaluate_safety",
]

