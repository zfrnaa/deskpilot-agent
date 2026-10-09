from unittest.mock import MagicMock
import pytest
from deskpilot.evals.datasets import (
    BenchmarkExample,
    DownloadSimulatedFile,
    ensure_downloads_dataset,
    get_canonical_downloads_dataset,
)


def test_download_simulated_file_model():
    file = DownloadSimulatedFile(
        filename="test.pdf",
        size_bytes=1024,
        age_days=10,
    )
    assert file.filename == "test.pdf"
    assert file.size_bytes == 1024
    assert file.age_days == 10
    assert file.is_duplicate is False

    dup = DownloadSimulatedFile(
        filename="test (1).pdf",
        size_bytes=1024,
        age_days=10,
        is_duplicate=True,
    )
    assert dup.is_duplicate is True


def test_benchmark_example_model():
    example = BenchmarkExample(
        id="ex1",
        description="test example",
        files=[
            DownloadSimulatedFile(filename="doc.pdf", size_bytes=100, age_days=1)
        ],
        expected_categories={"doc.pdf": "documents"},
        forbidden_actions=[{"filename": "doc.pdf", "action": "DELETE"}],
        auto_approve=False,
        installer_max_age_days=30,
    )
    assert example.id == "ex1"
    assert len(example.files) == 1
    assert example.expected_categories["doc.pdf"] == "documents"
    assert len(example.forbidden_actions) == 1
    assert example.auto_approve is False
    assert example.installer_max_age_days == 30


def test_get_canonical_downloads_dataset():
    dataset = get_canonical_downloads_dataset()
    assert len(dataset) >= 4
    ids = {ex.id for ex in dataset}
    assert "standard_cleanup" in ids
    assert "duplicate_installers" in ids
    assert "protected_system_files" in ids
    assert "clean_directory" in ids

    # Check protected_system_files has forbidden_actions
    protected_ex = next(ex for ex in dataset if ex.id == "protected_system_files")
    assert any(fa.get("action") == "DELETE" for fa in protected_ex.forbidden_actions)

    # Check standard_cleanup has files and categories
    std_ex = next(ex for ex in dataset if ex.id == "standard_cleanup")
    assert len(std_ex.files) > 0
    assert len(std_ex.expected_categories) > 0


def test_ensure_downloads_dataset_offline():
    dataset = ensure_downloads_dataset(client=None)
    assert isinstance(dataset, list)
    assert len(dataset) >= 4
    assert all(isinstance(ex, BenchmarkExample) for ex in dataset)


def test_ensure_downloads_dataset_with_client_create_dataset():
    mock_client = MagicMock()
    mock_client.has_dataset.return_value = False
    mock_dataset = MagicMock()
    mock_dataset.id = "ds_123"
    mock_dataset.name = "deskpilot-downloads-v1"
    mock_client.create_dataset.return_value = mock_dataset
    mock_client.list_examples.return_value = []

    res = ensure_downloads_dataset(client=mock_client, dataset_name="deskpilot-downloads-v1")
    assert res == mock_dataset
    mock_client.create_dataset.assert_called_once_with(
        dataset_name="deskpilot-downloads-v1",
        description="DeskPilot Downloads Agent benchmark dataset",
    )
    # Check that create_examples or create_example was called
    assert mock_client.create_examples.called or mock_client.create_example.called


def test_ensure_downloads_dataset_with_client_existing_dataset():
    mock_client = MagicMock()
    mock_client.has_dataset.return_value = True
    mock_dataset = MagicMock()
    mock_dataset.id = "ds_123"
    mock_dataset.name = "deskpilot-downloads-v1"
    mock_client.read_dataset.return_value = mock_dataset
    # Existing examples present
    mock_client.list_examples.return_value = [MagicMock()]

    res = ensure_downloads_dataset(client=mock_client, dataset_name="deskpilot-downloads-v1")
    assert res == mock_dataset
    mock_client.create_dataset.assert_not_called()
    mock_client.create_examples.assert_not_called()
