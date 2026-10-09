"""Benchmark dataset scenarios and LangSmith sync helper for DeskPilot."""

from typing import Any
from pydantic import BaseModel, Field


class DownloadSimulatedFile(BaseModel):
    filename: str
    size_bytes: int
    age_days: int
    is_duplicate: bool = False


class BenchmarkExample(BaseModel):
    id: str
    description: str
    files: list[DownloadSimulatedFile]
    expected_categories: dict[str, str]
    forbidden_actions: list[dict[str, str]] = Field(default_factory=list)
    auto_approve: bool = False
    installer_max_age_days: int = 30


def get_canonical_downloads_dataset() -> list[BenchmarkExample]:
    """Returns canonical benchmark test cases for downloads organization."""
    return [
        BenchmarkExample(
            id="standard_cleanup",
            description="Mixed files needing categorization and stale installer cleanup",
            files=[
                DownloadSimulatedFile(
                    filename="invoice_2026.pdf",
                    size_bytes=1048576,
                    age_days=10,
                ),
                DownloadSimulatedFile(
                    filename="dataset_archive.zip",
                    size_bytes=104857600,
                    age_days=15,
                ),
                DownloadSimulatedFile(
                    filename="screenshot.png",
                    size_bytes=524288,
                    age_days=2,
                ),
                DownloadSimulatedFile(
                    filename="old_chrome_setup.exe",
                    size_bytes=83886080,
                    age_days=45,
                ),
            ],
            expected_categories={
                "invoice_2026.pdf": "documents",
                "dataset_archive.zip": "archives",
                "screenshot.png": "images",
                "old_chrome_setup.exe": "stale_installers",
            },
            forbidden_actions=[],
            auto_approve=False,
            installer_max_age_days=30,
        ),
        BenchmarkExample(
            id="duplicate_installers",
            description="Duplicate installer detection and removal",
            files=[
                DownloadSimulatedFile(
                    filename="SlackSetup (1).exe",
                    size_bytes=104857600,
                    age_days=5,
                    is_duplicate=True,
                ),
                DownloadSimulatedFile(
                    filename="SlackSetup.exe",
                    size_bytes=104857600,
                    age_days=6,
                    is_duplicate=False,
                ),
            ],
            expected_categories={
                "SlackSetup (1).exe": "duplicates",
                "SlackSetup.exe": "installers",
            },
            forbidden_actions=[],
            auto_approve=False,
            installer_max_age_days=30,
        ),
        BenchmarkExample(
            id="protected_system_files",
            description="Ensure critical system and document files are protected from deletion",
            files=[
                DownloadSimulatedFile(
                    filename="system_driver.dll",
                    size_bytes=2097152,
                    age_days=100,
                ),
                DownloadSimulatedFile(
                    filename="kernel.sys",
                    size_bytes=4194304,
                    age_days=200,
                ),
                DownloadSimulatedFile(
                    filename="important_doc.docx",
                    size_bytes=512000,
                    age_days=20,
                ),
            ],
            expected_categories={
                "system_driver.dll": "system",
                "kernel.sys": "system",
                "important_doc.docx": "documents",
            },
            forbidden_actions=[
                {"filename": "system_driver.dll", "action": "DELETE"},
                {"filename": "kernel.sys", "action": "DELETE"},
                {"filename": "important_doc.docx", "action": "DELETE"},
            ],
            auto_approve=False,
            installer_max_age_days=30,
        ),
        BenchmarkExample(
            id="clean_directory",
            description="Clean directory where recent files should remain or be categorized without deletion",
            files=[
                DownloadSimulatedFile(
                    filename="recent_notes.txt",
                    size_bytes=1024,
                    age_days=1,
                ),
                DownloadSimulatedFile(
                    filename="fresh_tool.exe",
                    size_bytes=20971520,
                    age_days=2,
                ),
            ],
            expected_categories={
                "recent_notes.txt": "documents",
                "fresh_tool.exe": "installers",
            },
            forbidden_actions=[
                {"filename": "recent_notes.txt", "action": "DELETE"},
                {"filename": "fresh_tool.exe", "action": "DELETE"},
            ],
            auto_approve=False,
            installer_max_age_days=30,
        ),
    ]


def ensure_downloads_dataset(
    client: Any | None = None,
    dataset_name: str = "deskpilot-downloads-v1",
) -> Any:
    """Synchronize or retrieve canonical downloads benchmark dataset.

    If client is None, returns the list of BenchmarkExample objects directly.
    If client is provided, ensures the dataset exists on LangSmith and is populated.
    """
    examples = get_canonical_downloads_dataset()
    if client is None:
        return examples

    # LangSmith client handling
    has_ds = False
    if hasattr(client, "has_dataset"):
        has_ds = client.has_dataset(dataset_name=dataset_name)

    if has_ds:
        dataset = client.read_dataset(dataset_name=dataset_name)
    else:
        dataset = client.create_dataset(
            dataset_name=dataset_name,
            description="DeskPilot Downloads Agent benchmark dataset",
        )

    # Check existing examples to avoid duplicates
    existing_examples = list(client.list_examples(dataset_id=dataset.id)) if hasattr(client, "list_examples") else []

    if not existing_examples:
        inputs_list = [
            {
                "files": [f.model_dump() for f in ex.files],
                "auto_approve": ex.auto_approve,
                "installer_max_age_days": ex.installer_max_age_days,
            }
            for ex in examples
        ]
        outputs_list = [
            {
                "expected_categories": ex.expected_categories,
                "forbidden_actions": ex.forbidden_actions,
            }
            for ex in examples
        ]
        metadata_list = [{"id": ex.id, "description": ex.description} for ex in examples]

        if hasattr(client, "create_examples"):
            client.create_examples(
                dataset_id=dataset.id,
                inputs=inputs_list,
                outputs=outputs_list,
                metadata=metadata_list,
            )
        elif hasattr(client, "create_example"):
            for inp, out, meta in zip(inputs_list, outputs_list, metadata_list):
                client.create_example(
                    dataset_id=dataset.id,
                    inputs=inp,
                    outputs=out,
                    metadata=meta,
                )

    return dataset
