from hc_data_platform.collection_tasks.deletion import TaskObjectPurger
from .oss_client import OssBotoCompatClient


class DirectoryObjectPurger(TaskObjectPurger):
    """Delete versions only for exact keys in the committed directory manifest."""

    def purge(self, manifest):
        manifest = {name: list(values) for name, values in manifest.items()}
        model_files = [path for path in manifest["local_files"] if path.startswith("model://")]
        model_parts = [item for item in manifest["multiparts"] if item[0].startswith("model://")]
        if model_files or model_parts:
            models = self.models()
            for path, upload in model_parts:
                models.abort_multipart(path.removeprefix("model://"), upload)
            for path in model_files:
                models.delete_object(path.removeprefix("model://"))
            manifest["multiparts"] = [
                item for item in manifest["multiparts"] if item not in model_parts
            ]
            manifest["local_files"] = [
                path for path in manifest["local_files"] if path not in model_files
            ]
        if not manifest["objects"] and not manifest["multiparts"]:
            if manifest["local_files"]:
                self.local_artifacts.purge(manifest["local_files"])
            return
        client = self.client
        native = isinstance(client, OssBotoCompatClient)
        if native:
            routed = client._bucket
            physical = getattr(routed, "remote", routed)
            sdk = physical._delegate
            versioned = sdk.get_bucket_versioning().status in {"Enabled", "Suspended"}
        else:
            versioned = client.get_bucket_versioning(Bucket=self.bucket).get("Status") in {
                "Enabled",
                "Suspended",
            }
        if not versioned:
            return super().purge(manifest)
        for key, upload in manifest["multiparts"]:
            try:
                client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload)
            except Exception as exc:
                if getattr(exc, "response", {}).get("Error", {}).get("Code") != "NoSuchUpload":
                    raise
        for key in manifest["objects"]:
            if native:
                # The routed client also removes any platform-owned cached copy.
                client.delete_object(Bucket=self.bucket, Key=key)
                physical_key = physical.physical_key(key)
                while True:
                    page = sdk.list_object_versions(prefix=physical_key, max_keys=100)
                    versions = [
                        i.versionid
                        for i in (*page.versions, *page.delete_marker)
                        if i.key == physical_key
                    ]
                    if not versions:
                        break
                    for version in versions:
                        sdk.delete_object(physical_key, params={"versionId": version})
            else:
                while True:
                    page = client.list_object_versions(Bucket=self.bucket, Prefix=key, MaxKeys=100)
                    versions = [
                        i["VersionId"]
                        for i in (*page.get("Versions", []), *page.get("DeleteMarkers", []))
                        if i["Key"] == key
                    ]
                    if not versions:
                        break
                    for version in versions:
                        client.delete_object(Bucket=self.bucket, Key=key, VersionId=version)
            if self.size(key) is not None:
                raise RuntimeError("Directory object removal not confirmed")
        if manifest["local_files"]:
            self.local_artifacts.purge(manifest["local_files"])
