"""Render the Episode package fragment from production-composed runtime routes."""

from pathlib import Path

from hc_data_platform.core.openapi import render, runtime_document


def main():
    runtime = runtime_document()
    document = {
        "openapi": "3.1.0",
        "info": {"title": "HC Episode package contract", "version": "2026-09-29"},
        "paths": {
            path: item
            for path, item in runtime["paths"].items()
            if any(
                isinstance(operation, dict) and "episode_data" in operation.get("tags", [])
                for operation in item.values()
            )
        },
        "components": {
            # StepRecord and the shared errors remain owned by existing fragments.
            "schemas": {
                name: runtime["components"]["schemas"][name]
                for name in (
                    "DataVersionRef",
                    "EpisodeIdentity",
                    "PackageStepWindow",
                    "EpisodePreviewGrant",
                    "DataReviewTagReference",
                    "DataReviewLocation",
                    "SubmitEpisodeDataReview",
                    "EpisodeDataReview",
                    "EpisodeDataReviewPage",
                    "EpisodeDataReviewAction",
                    "RuleExecution",
                    "QcRuleEvidence",
                    "EpisodeQualityView",
                )
            }
        },
    }
    Path("openapi/episode_store.yaml").write_text(render(document), encoding="utf-8")


if __name__ == "__main__":
    main()
