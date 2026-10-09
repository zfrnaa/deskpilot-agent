"""DeskPilot Evaluation Harness package."""

from deskpilot.evals.datasets import (
    BenchmarkExample,
    DownloadSimulatedFile,
    ensure_downloads_dataset,
    get_canonical_downloads_dataset,
)

__all__ = [
    "BenchmarkExample",
    "DownloadSimulatedFile",
    "ensure_downloads_dataset",
    "get_canonical_downloads_dataset",
]
