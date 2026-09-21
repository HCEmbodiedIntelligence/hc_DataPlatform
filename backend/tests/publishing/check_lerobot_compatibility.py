"""Offline native LeRobot/ACT compatibility check, run in a separate LeRobot environment.

Example (Python 3.12 with lerobot[dataset,training]==0.6.1):
    python check_lerobot_compatibility.py dataset.zip --report result.json
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any


def check_archive(filename: Path, *, video_backend: str) -> dict[str, Any]:
    import torch
    from lerobot.configs.types import FeatureType
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    from lerobot.policies.act.configuration_act import ACTConfig
    from lerobot.policies.act.modeling_act import ACTPolicy
    from lerobot.policies.act.processor_act import make_act_pre_post_processors
    from lerobot.utils.feature_utils import dataset_to_policy_features

    torch.set_num_threads(2)
    torch.manual_seed(7)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="hc-act-export-check-") as directory:
        root = Path(directory)
        with zipfile.ZipFile(filename) as archive:
            for member in archive.namelist():
                if not (root / member).resolve().is_relative_to(root):
                    raise ValueError(f"Unsafe ZIP member: {member}")
            archive.extractall(root)
        info = json.loads((root / "meta/info.json").read_text())
        dataset = LeRobotDataset(
            "local/compatibility-check",
            root=root,
            video_backend=video_backend,
            delta_timestamps={"action": [i / info["fps"] for i in range(4)]},
        )
        features = dataset_to_policy_features(dataset.features)
        config = ACTConfig(
            input_features={
                key: value for key, value in features.items() if value.type != FeatureType.ACTION
            },
            output_features={
                key: value for key, value in features.items() if value.type == FeatureType.ACTION
            },
            device="cpu",
            push_to_hub=False,
            chunk_size=4,
            n_action_steps=4,
            pretrained_backbone_weights=None,
            dim_model=64,
            n_heads=4,
            dim_feedforward=128,
            n_encoder_layers=1,
            n_decoder_layers=1,
            n_vae_encoder_layers=1,
        )
        preprocess, postprocess = make_act_pre_post_processors(config, dataset.meta.stats)
        policy = ACTPolicy(config)
        optimizer = torch.optim.AdamW(policy.get_optim_params(), lr=1e-4)
        losses = []
        loader = torch.utils.data.DataLoader(dataset, batch_size=2, num_workers=0)
        batch: dict[str, Any] = {}
        for step, raw_batch in enumerate(loader):
            if step == 3:
                break
            batch = preprocess(raw_batch)
            for key in features:
                assert torch.isfinite(batch[key]).all(), key
            policy.train()
            optimizer.zero_grad()
            loss, loss_info = policy.forward(batch)
            assert torch.isfinite(loss), loss_info
            loss.backward()
            parameters = [p for p in policy.parameters() if p.grad is not None]
            assert parameters and all(torch.isfinite(p.grad).all() for p in parameters)
            before = parameters[0].detach().clone()
            optimizer.step()
            assert not torch.equal(before, parameters[0].detach())
            losses.append(float(loss.detach()))
        assert losses, "Dataset must contain at least one training batch"
        tail = dataset[len(dataset) - 1]
        assert tail["action_is_pad"].tolist() == [False, True, True, True]
        policy.eval()
        with torch.no_grad():
            prediction = postprocess(policy.predict_action_chunk(batch))
        assert torch.isfinite(prediction).all()
        return {
            "file": str(filename),
            "sha256": hashlib.sha256(filename.read_bytes()).hexdigest(),
            "lerobot_version": importlib.metadata.version("lerobot"),
            "torch_version": torch.__version__,
            "video_backend": video_backend,
            "frames": len(dataset),
            "cameras": len(dataset.meta.video_keys),
            "features": list(features),
            "action_batch_shape": list(batch["action"].shape),
            "prediction_shape": list(prediction.shape),
            "tail_padding": tail["action_is_pad"].tolist(),
            "optimizer_steps": len(losses),
            "losses": losses,
            "seconds": round(time.monotonic() - started, 3),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="+", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--video-backend", choices=("pyav", "torchcodec"), default="pyav")
    args = parser.parse_args()
    results = []
    for archive in args.archives:
        result = check_archive(archive, video_backend=args.video_backend)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        results.append(result)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
