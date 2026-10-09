"""Evaluation runner and sandboxed scenario prediction harness for DeskPilot."""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from deskpilot.agent_tasks.downloads_agent.actions import (
    format_plan_summary,
    get_item_action_key,
)
from deskpilot.agent_tasks.downloads_agent.graph import build_downloads_hygiene_graph
from deskpilot.agent_tasks.downloads_agent.state import (
    DownloadFileCategory,
    DownloadItem,
    ProposedAction,
    create_initial_state,
)
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

logger = logging.getLogger(__name__)


def _map_category_to_str(cat: DownloadFileCategory, item: DownloadItem) -> str:
    """Map internal category or proposed action to standard evaluation category string."""
    if item.proposed_action == ProposedAction.DELETE and item.category == DownloadFileCategory.INSTALLER:
        return "stale_installers"
    if item.category == DownloadFileCategory.DUPLICATE:
        return "duplicates"
    if item.category == DownloadFileCategory.DOCUMENT:
        return "documents"
    if item.category == DownloadFileCategory.ARCHIVE:
        return "archives"
    if item.category == DownloadFileCategory.MEDIA:
        ext = item.path.suffix.lower()
        if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico"}:
            return "images"
        return "media"
    if item.proposed_action == ProposedAction.KEEP:
        return "keep"
    return cat.value


def _populate_sandbox_files(sandbox_path: Path, files_input: list[Any]) -> None:
    """Populate synthetic files in the sandbox directory."""
    for file_spec in files_input:
        if isinstance(file_spec, DownloadSimulatedFile):
            fn = file_spec.filename
            sz = file_spec.size_bytes
            age = file_spec.age_days
        elif isinstance(file_spec, dict):
            fn = file_spec.get("filename", "unknown.tmp")
            sz = int(file_spec.get("size_bytes", 1024))
            age = float(file_spec.get("age_days", 0))
        else:
            fn = getattr(file_spec, "filename", "unknown.tmp")
            sz = int(getattr(file_spec, "size_bytes", 1024))
            age = float(getattr(file_spec, "age_days", 0))

        file_path = sandbox_path / fn
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Write dummy content with requested size
        content = b"x" * min(sz, 65536)
        file_path.write_bytes(content)

        if age:
            mtime = time.time() - (age * 86400.0)
            os.utime(file_path, (mtime, mtime))


def predict_downloads_scenario(
    inputs: dict[str, Any],
    temp_root: Path | None = None,
) -> dict[str, Any]:
    """Execute downloads agent workflow in an isolated sandbox directory and extract structured eval outputs."""
    raw_files = inputs.get("files", [])
    auto_approve = inputs.get("auto_approve", False)
    installer_max_age_days = inputs.get("installer_max_age_days", 30)

    # Determine whether we manage a temporary directory context
    if temp_root is not None:
        sandbox_path = Path(temp_root)
        sandbox_path.mkdir(parents=True, exist_ok=True)
        return _run_scenario_in_directory(
            sandbox_path=sandbox_path,
            raw_files=raw_files,
            auto_approve=auto_approve,
            installer_max_age_days=installer_max_age_days,
        )
    else:
        with tempfile.TemporaryDirectory(prefix="deskpilot_eval_") as tmp_dir:
            sandbox_path = Path(tmp_dir)
            return _run_scenario_in_directory(
                sandbox_path=sandbox_path,
                raw_files=raw_files,
                auto_approve=auto_approve,
                installer_max_age_days=installer_max_age_days,
            )


def _run_scenario_in_directory(
    sandbox_path: Path,
    raw_files: list[Any],
    auto_approve: bool,
    installer_max_age_days: int,
) -> dict[str, Any]:
    """Helper executing graph and assembling output metrics."""
    _populate_sandbox_files(sandbox_path, raw_files)

    archive_dir = sandbox_path / "Archive"
    graph = build_downloads_hygiene_graph(auto_approve=auto_approve)
    initial_state = create_initial_state(
        downloads_dir=sandbox_path,
        archive_dir=archive_dir,
        installer_max_age_days=installer_max_age_days,
        auto_approve=auto_approve,
    )

    result_state = graph.invoke(initial_state)

    items: list[DownloadItem] = result_state.get("items", [])
    action_plan = result_state.get("action_plan", {})
    approved_actions = result_state.get("approved_actions", [])
    errors = result_state.get("errors", [])
    total_freed = result_state.get("total_bytes_freed", 0)
    total_moved = result_state.get("total_files_moved", 0)

    # Extract proposed_actions as list of dicts: {"filename": ..., "action": ..., "destination": ...}
    proposed_actions: list[dict[str, Any]] = []
    proposed_categories: dict[str, str] = {}
    for item in items:
        cat_str = _map_category_to_str(item.category, item)
        proposed_categories[item.filename] = cat_str
        if item.proposed_action != ProposedAction.KEEP:
            act_dict: dict[str, Any] = {
                "filename": item.filename,
                "action": item.proposed_action.value.upper(),
                "category": cat_str,
            }
            if item.proposed_action == ProposedAction.ARCHIVE:
                act_dict["destination"] = cat_str
            proposed_actions.append(act_dict)

    # Executed actions list
    executed_actions: list[dict[str, Any]] = []
    if auto_approve and approved_actions:
        for key in approved_actions:
            group = action_plan.get(key, [])
            for item in group:
                cat_str = _map_category_to_str(item.category, item)
                executed_actions.append(
                    {
                        "filename": item.filename,
                        "action": item.proposed_action.value.upper(),
                        "category": cat_str,
                    }
                )

    plan_summary_list = format_plan_summary(action_plan)
    plan_summary_str = " | ".join(plan_summary_list) if plan_summary_list else "No actions proposed."

    return {
        "proposed_actions": proposed_actions,
        "proposed_categories": proposed_categories,
        "executed_actions": executed_actions,
        "plan_summary": plan_summary_str,
        "errors": errors,
        "total_bytes_freed": total_freed,
        "total_files_moved": total_moved,
        "auto_approve": auto_approve,
    }


def run_downloads_evaluation(
    client: Any | None = None,
    dataset_name: str = "deskpilot-downloads-v1",
    experiment_prefix: str = "downloads-baseline",
    local_only: bool = False,
    print_report: bool = True,
) -> dict[str, Any]:
    """Execute downloads benchmark evaluation locally or against LangSmith."""
    if local_only or client is None:
        return _run_local_evaluation(dataset_name=dataset_name, print_report=print_report)

    # LangSmith cloud evaluation mode
    try:
        import langsmith

        ensure_downloads_dataset(client, dataset_name=dataset_name)

        evaluators = [
            evaluate_safety,
            evaluate_categorization,
            evaluate_hitl_compliance,
            evaluate_reasoning_quality,
        ]

        def _target(inputs: dict[str, Any]) -> dict[str, Any]:
            return predict_downloads_scenario(inputs)

        experiment_results = langsmith.evaluate(
            _target,
            data=dataset_name,
            evaluators=evaluators,
            experiment_prefix=experiment_prefix,
            client=client,
        )

        url = getattr(experiment_results, "_url", None) or getattr(experiment_results, "url", None)
        if not url and hasattr(experiment_results, "experiment_name"):
            url = f"https://smith.langchain.com/o/default/projects/p/{dataset_name}"

        if print_report:
            console = Console()
            console.print(f"\n[bold green]✓ LangSmith evaluation finished![/bold green]")
            if url:
                console.print(f"[bold cyan]Experiment URL:[/bold cyan] {url}")

        return {
            "local": False,
            "dataset": dataset_name,
            "experiment_url": str(url) if url else "",
            "experiment_results": experiment_results,
        }
    except Exception as exc:
        logger.warning("LangSmith evaluation failed (%s); falling back to local evaluation.", exc)
        if print_report:
            Console().print(f"[yellow]Warning: LangSmith call failed ({exc}). Falling back to local evaluation.[/yellow]")
        return _run_local_evaluation(dataset_name=dataset_name, print_report=print_report)


def _run_local_evaluation(dataset_name: str, print_report: bool = True) -> dict[str, Any]:
    """Run local deterministic evaluation on the canonical dataset."""
    examples = get_canonical_downloads_dataset()
    results: list[dict[str, Any]] = []

    sum_scores: dict[str, float] = {
        "safety": 0.0,
        "categorization": 0.0,
        "hitl_compliance": 0.0,
        "reasoning_quality": 0.0,
    }

    for ex in examples:
        inputs = {
            "files": [f.model_dump() for f in ex.files],
            "auto_approve": ex.auto_approve,
            "installer_max_age_days": ex.installer_max_age_days,
        }
        outputs = predict_downloads_scenario(inputs)

        run_obj = {"outputs": outputs}
        example_obj = ex

        safety_res = evaluate_safety(run_obj, example_obj)
        cat_res = evaluate_categorization(run_obj, example_obj)
        hitl_res = evaluate_hitl_compliance(run_obj, example_obj)
        reason_res = evaluate_reasoning_quality(run_obj, example_obj)

        sum_scores["safety"] += safety_res["score"]
        sum_scores["categorization"] += cat_res["score"]
        sum_scores["hitl_compliance"] += hitl_res["score"]
        sum_scores["reasoning_quality"] += reason_res["score"]

        comments = []
        if safety_res["score"] < 1.0:
            comments.append(safety_res["comment"])
        if cat_res["score"] < 1.0:
            comments.append(cat_res["comment"])
        if hitl_res["score"] < 1.0:
            comments.append(hitl_res["comment"])
        if reason_res["score"] < 1.0:
            comments.append(reason_res["comment"])

        results.append(
            {
                "id": ex.id,
                "safety": safety_res["score"],
                "categorization": cat_res["score"],
                "hitl_compliance": hitl_res["score"],
                "reasoning_quality": reason_res["score"],
                "comments": "; ".join(comments) if comments else "All metrics passed.",
            }
        )

    count = len(examples) if examples else 1
    mean_scores = {k: v / count for k, v in sum_scores.items()}

    if print_report:
        _print_local_report(results, mean_scores)

    return {
        "local": True,
        "dataset": dataset_name,
        "scores": mean_scores,
        "results": results,
    }


def _print_local_report(results: list[dict[str, Any]], mean_scores: dict[str, float]) -> None:
    """Print Rich summary table of local benchmark evaluation results."""
    console = Console()
    table = Table(title="[bold cyan]DeskPilot Downloads Hygiene Benchmark Report[/bold cyan]")
    table.add_column("Scenario ID", style="bold white", width=25)
    table.add_column("Safety", justify="right", width=10)
    table.add_column("Categorization", justify="right", width=15)
    table.add_column("HITL", justify="right", width=10)
    table.add_column("Reasoning", justify="right", width=12)
    table.add_column("Comments", style="dim", width=40)

    def _fmt(score: float) -> str:
        if score >= 1.0:
            return f"[bold green]{score:.2f}[/bold green]"
        elif score >= 0.7:
            return f"[yellow]{score:.2f}[/yellow]"
        return f"[bold red]{score:.2f}[/bold red]"

    for r in results:
        table.add_row(
            r["id"],
            _fmt(r["safety"]),
            _fmt(r["categorization"]),
            _fmt(r["hitl_compliance"]),
            _fmt(r["reasoning_quality"]),
            r["comments"],
        )

    table.add_section()
    table.add_row(
        "[bold]MEAN SCORE[/bold]",
        _fmt(mean_scores["safety"]),
        _fmt(mean_scores["categorization"]),
        _fmt(mean_scores["hitl_compliance"]),
        _fmt(mean_scores["reasoning_quality"]),
        "[bold cyan]Benchmark Summary[/bold cyan]",
    )

    console.print()
    console.print(table)
    console.print()
