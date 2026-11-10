"""Confine the native OSS adapter to one logical object-store root."""

from __future__ import annotations

from copy import copy


def normalize_prefix(value: str) -> str:
    value = value.rstrip("/")
    if not value:
        return ""
    if (
        "\\" in value
        or any(ord(c) < 32 for c in value)
        or any(p in {"", ".", ".."} for p in value.split("/"))
    ):
        raise ValueError("invalid OSS key prefix")
    return value + "/"


class PrefixedOssBucket:
    _OBJECT_METHODS = frozenset(
        {
            "get_object_meta",
            "head_object",
            "get_object",
            "put_object",
            "delete_object",
            "init_multipart_upload",
            "upload_part",
            "list_parts",
            "complete_multipart_upload",
            "abort_multipart_upload",
        }
    )
    _BUCKET_METHODS = frozenset({"get_bucket_info", "get_bucket_stat"})

    def __init__(self, bucket, prefix: str, *, key_layout=None):
        self._delegate = bucket
        self.key_layout = key_layout
        self.key_prefix = normalize_prefix(prefix)
        self.bucket_name = bucket.bucket_name

    def physical_key(self, key: str, *, create_alias=False) -> str:
        if (
            not key
            or "://" in key
            or key.startswith("/")
            or "\\" in key
            or any(ord(c) < 32 for c in key)
            or any(p in {"", ".", ".."} for p in key.split("/"))
        ):
            raise ValueError("invalid logical OSS object key")
        if self.key_layout is not None:
            key = self.key_layout.physical(key, create=create_alias)
        return self.key_prefix + key

    def read_key(self, key: str) -> str:
        if key.startswith("oss://"):
            from .oss_reference import reference_key

            physical = reference_key(key, self.bucket_name, self.key_prefix)
            return self.physical_key(physical[len(self.key_prefix) :])
        return self.physical_key(key)

    def _logical(self, key: str, aliases=None) -> str:
        if not key.startswith(self.key_prefix):
            raise ValueError("OSS listing escaped configured prefix")
        logical = key[len(self.key_prefix) :]
        return self.key_layout.logical(logical, aliases) if self.key_layout is not None else logical

    def __getattr__(self, name: str):
        if name in self._BUCKET_METHODS:
            return getattr(self._delegate, name)
        if name not in self._OBJECT_METHODS:
            raise AttributeError(name)

        def invoke(key, *args, **kwargs):
            physical = (
                self.read_key(key)
                if name in {"get_object", "get_object_meta", "head_object"}
                else self.physical_key(
                    key, create_alias=name in {"put_object", "init_multipart_upload"}
                )
            )
            return getattr(self._delegate, name)(physical, *args, **kwargs)

        return invoke

    def sign_url(self, method, key, *args, **kwargs):
        physical = (
            self.read_key(key)
            if method.upper() in {"GET", "HEAD"}
            else self.physical_key(key, create_alias=method.upper() == "PUT")
        )
        return self._delegate.sign_url(method, physical, *args, **kwargs)

    def copy_object(self, source_bucket, source_key, target_key, *args, **kwargs):
        if source_bucket != self.bucket_name:
            raise ValueError("cross-bucket copies are not supported by the scoped adapter")
        return self._delegate.copy_object(
            source_bucket,
            self.read_key(source_key),
            self.physical_key(target_key, create_alias=True),
            *args,
            **kwargs,
        )

    def list_objects_v2(self, prefix="", **kwargs):
        logical_prefix = normalize_prefix(prefix) if prefix.endswith("/") else prefix
        if logical_prefix:
            self.physical_key(logical_prefix.rstrip("/"))
        if kwargs.get("start_after"):
            kwargs["start_after"] = self.physical_key(kwargs["start_after"])
        aliases = self.key_layout.all() if self.key_layout is not None else None
        query_prefix = logical_prefix
        if aliases:
            from .oss_layout import replace_prefix

            query_prefix = replace_prefix(logical_prefix.rstrip("/"), aliases)
            if logical_prefix.endswith("/") and query_prefix:
                query_prefix += "/"
            if query_prefix == logical_prefix and any(
                p.startswith(logical_prefix) for p in aliases
            ):
                query_prefix = logical_prefix.split("/", 1)[0] + "/" if logical_prefix else ""
        result = copy(
            self._delegate.list_objects_v2(
                prefix=self.key_prefix + query_prefix,
                **kwargs,
            )
        )
        result.object_list = [copy(item) for item in result.object_list]
        for item in result.object_list:
            item.key = self._logical(item.key, aliases)
        result.object_list = [
            item for item in result.object_list if item.key and item.key.startswith(logical_prefix)
        ]
        if hasattr(result, "prefix_list"):
            result.prefix_list = [self._logical(p, aliases) for p in result.prefix_list]
        return result
