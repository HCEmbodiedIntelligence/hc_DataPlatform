"""Last-use ledger for regenerable caches kept in the local MinIO deployment."""
import json
import os
import time

from hc_data_platform.core.context import current_request_context
from .local_cache import PREVIEW_CACHE_KINDS


class S3CacheAccessClient:
    def __init__(self, client, cache, bucket):
        self.remote, self.lifecycle_cache, self.bucket = client, cache, bucket

    def record(self, key):
        if not isinstance(key, str) or key.split('/')[0] not in PREVIEW_CACHE_KINDS:
            return
        try:
            context = current_request_context()
        except RuntimeError:
            return
        scope = [context.organization_id, context.project_id, context.region_code]
        if not all(scope):
            return
        cache = self.lifecycle_cache
        with cache.lock(key):
            directory = cache.directory(key)
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / 'meta.json'
            if not path.exists():
                head = self.remote.head_object(Bucket=self.bucket, Key=key)
                path.write_text(json.dumps(dict(key=key, size=head['ContentLength'], scope=scope,
                    remote=True, modified=time.time())))
            os.utime(path, None)

    def generate_presigned_url(self, operation, **kwargs):
        result = self.remote.generate_presigned_url(operation, **kwargs)
        if operation == 'get_object':
            self.record(kwargs.get('Params', {}).get('Key'))
        return result

    def __getattr__(self, name):
        operation = getattr(self.remote, name)
        if name not in {'get_object', 'put_object', 'complete_multipart_upload'}:
            return operation
        def call(**kwargs):
            result = operation(**kwargs)
            self.record(kwargs.get('Key'))
            return result
        return call
