from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hc_data_platform import runtime
from hc_data_platform.core.config import Settings
from hc_data_platform.platform_ops import object_store_config


@pytest.mark.parametrize(
    "factory", [runtime.activity_dependencies, runtime.media_activity_dependencies]
)
def test_worker_uses_page_configuration_before_building_adapters(monkeypatch, factory):
    boot = Settings(
        _env_file=None,
        object_store_provider="oss",
        object_store_endpoint="",
        object_store_bucket="",
        object_store_access_key="",
        object_store_secret_key="",
    )
    saved = boot.model_copy(
        update={
            "object_store_endpoint": "https://oss-cn-beijing.aliyuncs.com",
            "object_store_bucket": "page-configured-bucket",
        }
    )
    monkeypatch.setattr(runtime, "get_settings", lambda: boot)
    loader = Mock(return_value=(saved, 2))
    monkeypatch.setattr(object_store_config, "load_persisted_object_store_settings", loader)

    class ReachedAdapterConstruction(Exception):
        pass

    def capture(dsn):
        loader.assert_called_once_with(boot)
        raise ReachedAdapterConstruction

    monkeypatch.setattr(runtime, "psycopg_connection_factory", capture)
    with pytest.raises(ReachedAdapterConstruction):
        factory()


def test_lance_root_stays_inside_oss_prefix():
    settings = SimpleNamespace(
        lance_root_uri=None,
        object_store_provider="oss",
        object_store_bucket="test",
        object_store_prefix="hc_dataplatform",
    )
    assert runtime._lance_root(settings) == "oss://test/hc_dataplatform/lance"
