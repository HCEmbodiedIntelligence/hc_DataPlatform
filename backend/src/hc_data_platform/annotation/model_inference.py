"""Vision model inference on numbered frames from a committed Episode."""

import base64
import hashlib
import io
import json
from urllib.parse import urlsplit
from uuid import uuid4

import av
import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from hc_data_platform.core.errors import problem
from hc_data_platform.data_formats.lerobot_layout import VIDEO_PATH

from .models import AnnotationTag


def failure(status, code, detail):
    return problem(status=status, code=code, title="Model annotation", detail=detail)


class ModelConnection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    endpoint: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=256)
    sample_frames: int = Field(default=16, ge=2, le=48)
    camera: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value):
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
        ):
            raise ValueError("请填写 HTTPS Chat Completions 接口完整地址")
        return value


class ModelSpan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    label_id: int = Field(ge=1)
    start_step: int = Field(ge=0)
    end_step: int = Field(gt=0)


class ModelAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segments: list[ModelSpan] = Field(max_length=2000)


def sample_images(package, config):
    info = package.document("meta/info.json")
    cameras = sorted(
        name
        for name, feature in info["features"].items()
        if feature["dtype"] == "video" and not feature.get("info", {}).get("is_depth_map")
    )
    camera = config.camera or next(iter(cameras), None)
    if camera not in cameras:
        raise failure(422, "MODEL_CAMERA_UNAVAILABLE", "此 Episode 没有所选 RGB 相机画面")
    count = package.receipt.data.frame_count
    steps = {i * (count - 1) // (config.sample_frames - 1) for i in range(config.sample_frames)}
    relative = VIDEO_PATH.format(video_key=camera, chunk_index=0, file_index=0)
    images = []
    with package.file(relative) as path, av.open(str(path)) as video:
        for step, frame in enumerate(video.decode(video=0)):
            if step not in steps:
                continue
            image = frame.to_image()
            image.thumbnail((640, 480))
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=80)
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            images.extend(
                [
                    {"type": "text", "text": f"{camera} · step {step}"},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                    },
                ]
            )
            if step == count - 1:
                break
    if len(images) != 2 * len(steps):
        raise failure(409, "MODEL_FRAMES_INCOMPLETE", "相机视频与 Episode 帧数不一致")
    return images


def completion(config, key, messages):
    try:
        with (
            httpx.Client(timeout=httpx.Timeout(90, connect=10), follow_redirects=False) as client,
            client.stream(
                "POST",
                config.endpoint,
                headers={"Authorization": "Bearer " + key},
                json={"model": config.model, "messages": messages},
            ) as result,
        ):
            result.raise_for_status()
            output = bytearray()
            for chunk in result.iter_bytes():
                output.extend(chunk)
                if len(output) > 2 * 1024 * 1024:
                    raise ValueError("response exceeds limit")
        content = json.loads(output)["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("empty content")
        return content
    except httpx.HTTPStatusError as exc:
        raise failure(
            502,
            "MODEL_PROVIDER_ERROR",
            f"模型服务返回 HTTP {exc.response.status_code}，请检查配置和额度",
        ) from None
    except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError):
        raise failure(502, "MODEL_RESPONSE_INVALID", "模型服务超时或返回了无效响应") from None


def predict(config, key, package, labels, guide, schema):
    vocabulary = dict(enumerate(labels, 1))
    context = {
        "标注规范": guide.instructions,
        "提示词": guide.prompt,
        "标签候选": vocabulary,
        "总帧数": package.receipt.data.frame_count,
        "任务标签规范": schema.document.model_dump(mode="json"),
    }
    system = (
        "根据按时间排序的 Episode 画面进行标注。只返回 JSON 对象 "
        '{"segments":[{"label_id":1,"start_step":0,"end_step":1}]}。'
        "label_id 只能从标签候选中选取。使用从零开始、左闭右开的帧区间。"
        "没有足够证据时跳过该段，可返回空数组。"
    )
    content = [
        {"type": "text", "text": json.dumps(context, ensure_ascii=False)},
        *sample_images(package, config),
    ]
    raw = completion(
        config, key, [{"role": "system", "content": system}, {"role": "user", "content": content}]
    )
    return parse_tags(raw, vocabulary, package.receipt.data.frame_count)


def parse_tags(raw, vocabulary, count):
    try:
        answer = ModelAnswer.model_validate_json(raw)
        tags = []
        for span in answer.segments:
            label = vocabulary[span.label_id]
            if not span.start_step < span.end_step <= count:
                raise ValueError("invalid interval")
            tag_id = "model-label-" + hashlib.sha256(label.encode()).hexdigest()[:24]
            tags.append(
                AnnotationTag(
                    annotation_id=str(uuid4()),
                    tag_id=tag_id,
                    label=label,
                    path=(tag_id,),
                    start_step=span.start_step,
                    end_step=span.end_step,
                )
            )
        return tags
    except (KeyError, ValueError):
        raise failure(
            422, "MODEL_ANNOTATION_INVALID", "模型返回的标签或帧区间无效，本次结果未写入"
        ) from None
