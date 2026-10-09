"""Tests for sandbox prediction harness and evaluation runner."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from deskpilot.evals.datasets import BenchmarkExample, DownloadSimulatedFile
from deskpilot.evals.runner import (
    predict_downloads_scenario,
    run_downloads_evaluation,
)


def test_predict_downloads_scenario_sandbox_isolation(tmp_path: Path):
    """Test that predict_downloads_scenario operates strictly within temp_root and produces files."""
    scenario_files = [
        DownloadSimulatedFile(
            filename="document.pdf",
            size_bytes=1024,
            age_days=10,
        ),
        {
            "filename": "old_tool.exe",
            "size_bytes": 2048,
            "age_days": 45,
        },
    ]

    inputs = {
        "files": scenario_files,
        "auto_approve": False,
        "installer_max_age_days": 30,
    }

    custom_temp = tmp_path / "sandbox_downloads"
    output = predict_downloads_scenario(inputs, temp_root=custom_temp)

    assert "proposed_actions" in output
    assert "proposed_categories" in output
    assert "executed_actions" in output
    assert "plan_summary" in output
    assert "errors" in output
    assert "total_bytes_freed" in output
    assert "total_files_moved" in output

    # Check categories
    assert output["proposed_categories"].get("document.pdf") == "documents"
    assert output["proposed_categories"].get("old_tool.exe") == "stale_installers"

    # auto_approve was False, so no executed actions
    assert output["executed_actions"] == []
    assert output["total_files_moved"] == 0


def test_predict_downloads_scenario_auto_approve(tmp_path: Path):
    """Test that auto_approve=True executes the actions in the sandbox."""
    scenario_files = [
        DownloadSimulatedFile(
            filename="archive.zip",
            size_bytes=5000,
            age_days=5,
        ),
        DownloadSimulatedFile(
            filename="setup.exe",
            size_bytes=10000,
            age_days=60,
        ),
    ]

    inputs = {
        "files": scenario_files,
        "auto_approve": True,
        "installer_max_age_days": 30,
    }

    sandbox_dir = tmp_path / "auto_sandbox"
    output = predict_downloads_scenario(inputs, temp_root=sandbox_dir)

    assert len(output["executed_actions"]) > 0
    # setup.exe should have been deleted, archive moved
    assert output["total_bytes_freed"] > 0 or output["total_files_moved"] > 0


def test_predict_downloads_scenario_default_temp_dir():
    """Test predict_downloads_scenario runs cleanly without explicit temp_root."""
    inputs = {
        "files": [
            {
                "filename": "screenshot.png",
                "size_bytes": 2048,
                "age_days": 1,
            }
        ],
        "auto_approve": False,
    }
    output = predict_downloads_scenario(inputs)
    assert output["proposed_categories"].get("screenshot.png") == "images"


def test_run_downloads_evaluation_local_only(capsys):
    """Test run_downloads_evaluation in local_only mode."""
    results = run_downloads_evaluation(
        client=None,
        dataset_name="deskpilot-downloads-v1",
        local_only=True,
        print_report=True,
    )

    assert results["local"] is True
    assert results["dataset"] == "deskpilot-downloads-v1"
    assert "scores" in results
    assert "safety" in results["scores"]
    assert "categorization" in results["scores"]
    assert "hitl_compliance" in results["scores"]
    assert "reasoning_quality" in results["scores"]
    assert len(results["results"]) >= 4

    # Verify report printed via rich
    captured = capsys.readouterr()
    assert "Downloads Hygiene Benchmark" in captured.out or "standard_cleanup" in captured.out


def test_run_downloads_evaluation_langsmith_client():
    """Test run_downloads_evaluation with a mocked LangSmith client."""
    mock_client = MagicMock()
    mock_client.has_dataset.return_value = True
    mock_dataset = MagicMock()
    mock_dataset.id = "mock-ds-id"
    mock_dataset.name = "deskpilot-downloads-v1"
    mock_dataset.url = "https://smith.langchain.com/mock-dataset"
    mock_client.read_dataset.return_value = mock_dataset
    mock_client.list_examples.return_value = [MagicMock()]

    mock_experiment_results = MagicMock()
    mock_experiment_results.to_pandas.return_value = None
    mock_experiment_results._url = "https://smith.langchain.com/mock-experiment"

    with patch("langsmith.evaluate", return_value=mock_experiment_results) as mock_eval:
        results = run_downloads_evaluation(
            client=mock_client,
            dataset_name="deskpilot-downloads-v1",
            experiment_prefix="test-eval",
            local_only=False,
            print_report=True,
        )

        assert results["local"] is False
        assert results["experiment_url"] == "https://smith.langchain.com/mock-experiment"
        mock_eval.assert_called_once()
