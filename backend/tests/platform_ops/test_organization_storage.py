from types import SimpleNamespace
import pytest
from hc_data_platform.core.config import Settings
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.platform_ops.object_store_config import (
    InMemoryObjectStoreConfigurationRepository,
    ObjectStoreConfigurationService,
    ObjectStoreConfigurationUpdate,
)
from hc_data_platform.storage.organization_storage import (
    OrganizationStorage,
    ScopedBotoClient,
    ScopedObjectStore,
)


def test_storage_credentials_buckets_cache_and_no_fallback(tmp_path):
    settings = Settings(
        environment="test",
        runtime_backend="memory",
        object_store_owner_organization_id="01",
        object_store_provider="oss",
        object_store_endpoint="https://oss-cn-beijing.aliyuncs.com",
        object_store_public_endpoint="https://oss-cn-beijing.aliyuncs.com",
        object_store_bucket="first-bucket",
        object_store_region="cn-beijing",
        object_store_access_key="test-first",
        object_store_secret_key="fake-secret-1",
        object_store_cache_root=str(tmp_path / "cache"),
        object_store_prefix="existing/",
        _env_file=None,
    )
    repo = InMemoryObjectStoreConfigurationRepository()
    service = ObjectStoreConfigurationService(repo, settings)
    a = service.for_organization("01")
    b = service.for_organization("02")
    assert a.current().bucket == "first-bucket"
    assert not b.current().configured and not b.current().endpoint
    calls = []

    def build(config):
        def get_object(**kw):
            calls.append((config.object_store_access_key, kw["Bucket"], kw["Key"]))
            return calls[-1]

        fake = SimpleNamespace(get_object=get_object)
        return fake, fake, SimpleNamespace(name=config.object_store_bucket)

    resolver = OrganizationStorage(
        settings, repo, lambda project: "01" if project == "unique" else None, build
    )
    client = ScopedBotoClient(resolver, 0)
    objects = ScopedObjectStore(resolver, 2)

    def call(org, project=None):
        token = bind_request_context(RequestContext(organization_id=org, project_id=project))
        try:
            return client.get_object(Bucket="first-bucket", Key="shared/key"), objects.name
        finally:
            reset_request_context(token)

    assert call("01")[1] == "first-bucket"
    with pytest.raises(ProblemException):
        call("02")
    with pytest.raises(ProblemException):
        call(None, "ambiguous")
    assert call(None, "unique")[1] == "first-bucket"
    command = ObjectStoreConfigurationUpdate(
        expected_revision=0,
        provider="oss",
        endpoint="https://oss-cn-shanghai.aliyuncs.com",
        public_endpoint="https://oss-cn-shanghai.aliyuncs.com",
        bucket="second-bucket",
        region="cn-shanghai",
        access_key="test-second",
        secret_key="fake-secret-2",
    )
    b.save(command, actor_id="test-admin", request_id="test")
    assert call("02") == (("test-second", "second-bucket", "shared/key"), "second-bucket")
    b_config, _ = resolver.settings_for("02")
    assert (
        b_config.object_store_prefix == ""
        and b_config.object_store_cache_root != settings.object_store_cache_root
    )
    assert call("01")[0][0] == "test-first"
    with pytest.raises(ProblemException):
        call("03")


def test_persisted_legacy_bucket_is_reserved_and_scoped_revisions_apply_immediately():
    from hc_data_platform.platform_ops.object_store_config import ObjectStoreConfigurationError
    settings = Settings(environment="test", runtime_backend="memory", object_store_owner_organization_id="01",
                        object_store_provider="oss", object_store_endpoint="https://object-store-unconfigured.invalid", object_store_public_endpoint="https://object-store-unconfigured.invalid", object_store_access_key="unconfigured-access-key",
                        object_store_secret_key="unconfigured", object_store_bucket="unconfigured", _env_file=None)
    repo = InMemoryObjectStoreConfigurationRepository()
    service = ObjectStoreConfigurationService(repo, settings)
    command = ObjectStoreConfigurationUpdate(expected_revision=0, provider="oss",endpoint="https://example.invalid",
        public_endpoint="https://example.invalid", bucket="legacy-bucket",region="test",
        access_key="temporary-test",secret_key="temporary-test-secret")
    service.save(command, actor_id="test",request_id="test")
    resolver = OrganizationStorage(settings,repo,lambda _:None,lambda _:None)
    assert resolver.settings_for("01")[0].object_store_bucket == "legacy-bucket"
    assert service.for_organization("01").current().bucket == "legacy-bucket"
    second = service.for_organization("02")
    with pytest.raises(ObjectStoreConfigurationError): second.save(command,actor_id="test",request_id="test")
    unique = command.model_copy(update={"bucket":"other-bucket"})
    assert not second.save(unique,actor_id="test",request_id="test").activation_required
    with pytest.raises(ObjectStoreConfigurationError): service.for_organization("03").save(unique,actor_id="test",request_id="test")
    assert not service.for_organization("03").current().configured
