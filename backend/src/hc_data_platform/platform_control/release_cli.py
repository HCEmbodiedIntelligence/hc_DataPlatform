"""Credential-free release verification helpers for an external GitOps controller."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .canary import (
    CanaryComponentObservationV1,
    CanaryGateDecisionV1,
    CanaryThresholdsV1,
    evaluate_canary,
)
from .release_feed import ReleaseImagesV1
from .worker_versioning import WorkerVersioningPlanV1, install_compatible_build_routing


class CanaryEvaluationDocumentV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: Literal["hc-platform-canary-evaluation/v1"] = "hc-platform-canary-evaluation/v1"
    target_images: ReleaseImagesV1
    source_images: ReleaseImagesV1
    observations: tuple[CanaryComponentObservationV1, ...]
    thresholds: CanaryThresholdsV1 = CanaryThresholdsV1()


def evaluate_canary_document(document: CanaryEvaluationDocumentV1) -> CanaryGateDecisionV1:
    return evaluate_canary(
        document.observations,
        target_images=document.target_images,
        source_images=document.source_images,
        thresholds=document.thresholds,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HC external release-controller helpers")
    commands = parser.add_subparsers(dest="command", required=True)
    canary = commands.add_parser("canary", help="evaluate a bounded API/frontend metric window")
    canary.add_argument("--document", required=True, type=Path)
    temporal = commands.add_parser(
        "temporal-route", help="install compatible target build routing on fixed task queues"
    )
    temporal.add_argument("--document", required=True, type=Path)
    temporal.add_argument("--target", required=True)
    temporal.add_argument("--namespace", default="default")
    return parser


async def _temporal_route(document: Path, *, target: str, namespace: str) -> tuple[str, ...]:
    from temporalio.client import Client

    plan = WorkerVersioningPlanV1.model_validate_json(document.read_text(encoding="utf-8"))
    client = await Client.connect(target, namespace=namespace)
    return await install_compatible_build_routing(client, plan)


def main() -> None:
    args = _parser().parse_args()
    if args.command == "canary":
        document = CanaryEvaluationDocumentV1.model_validate_json(
            args.document.read_text(encoding="utf-8")
        )
        decision = evaluate_canary_document(document)
        print(decision.model_dump_json())
        if decision.action == "PROCEED":
            return
        raise SystemExit(20 if decision.action == "HOLD" else 30)
    updated = asyncio.run(
        _temporal_route(args.document, target=args.target, namespace=args.namespace)
    )
    print(json.dumps({"status": "COMPATIBLE_DEFAULT_INSTALLED", "task_queues": updated}))


__all__ = ["CanaryEvaluationDocumentV1", "evaluate_canary_document", "main"]
