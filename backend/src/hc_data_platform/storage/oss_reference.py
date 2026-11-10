"""Read-only source locators for existing objects in the configured OSS root."""

from urllib.parse import quote, unquote, urlsplit


def reference_uri(bucket: str, key: str) -> str:
    return f"oss://{bucket}/{quote(key, safe='/')}".rstrip("/")


def reference_key(uri: str, bucket: str, prefix: str = "") -> str:
    parsed = urlsplit(uri)
    key = unquote(parsed.path.lstrip("/"))
    if (
        parsed.scheme != "oss"
        or parsed.netloc != bucket
        or parsed.query
        or parsed.fragment
        or not key
        or "\\" in key
        or any(ord(c) < 32 for c in key)
        or any(p in {"", ".", ".."} for p in key.rstrip("/").split("/"))
        or (prefix and not key.startswith(prefix))
    ):
        raise ValueError("地址必须属于平台已配置的 OSS Bucket 和存储目录，且不能包含签名参数。")
    return key
