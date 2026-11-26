"""Select storage exclusively by the authorized request or activity's organization."""

from __future__ import annotations

from threading import RLock
from typing import Any
from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import problem
from hc_data_platform.platform_ops.object_store_config import (
    organization_storage_key,
    settings_object_store_configured,
)


class OrganizationStorage:
    def __init__(self, settings, repository, resolve_project, build):
        self.settings = settings
        self.repository = repository
        self.resolve_project = resolve_project
        self.build = build
        self.clients = {}
        self.lock = RLock()

    def organization(self):
        try:
            context = current_request_context()
        except RuntimeError:
            context = None
        if context and context.organization_id:
            return context.organization_id
        if context and context.project_id:
            # Legacy activities may have only a project. An ambiguous project
            # must be rescheduled with an explicit organization.
            org = self.resolve_project(context.project_id)
            if org:
                return org
        raise problem(
            status=403,
            code="STORAGE_ORGANIZATION_REQUIRED",
            title="Organization required",
            detail="读取存储前必须确定唯一的所属组织。",
        )

    def settings_for(self, org):
        stored = self.repository.current(
            organization_storage_key(self.settings.platform_environment_id, org)
        )
        owner = org == self.settings.object_store_owner_organization_id
        if stored is None:
            if owner:
                if settings_object_store_configured(self.settings):
                    return self.settings, ("environment", 0)
                legacy = self.repository.current(self.settings.platform_environment_id)
                if legacy is not None:
                    return self.settings.model_copy(update=legacy.settings_updates()), (
                        "legacy",
                        legacy.revision,
                    )
            raise problem(
                status=409,
                code="ORGANIZATION_STORAGE_UNCONFIGURED",
                title="Organization storage unconfigured",
                detail="请先为本组织配置 OSS。",
            )
        update = stored.settings_updates()
        if not owner:
            # Environment archive sources and local caches belong to the original deployment.
            update.update(
                object_store_prefix="",
                object_store_cache_root=None,
                object_store_local_path=None,
                object_store_browser_path=None,
                object_store_raw_browser_path=None,
                oss_import_archives={},
                oss_import_relocations={},
                lance_root_uri=None,
            )
        return self.settings.model_copy(update=update), ("organization", stored.revision)

    def resolve(self, kind):
        org = self.organization()
        with self.lock:
            config, revision = self.settings_for(org)
            entry = self.clients.get(org)
            if entry is None or entry[0] != revision:
                entry = (revision, self.build(config), config)
                self.clients[org] = entry
            return entry[1][kind], entry[2]


class ScopedObjectStore:
    def __init__(self, directory, kind):
        self.directory = directory
        self.kind = kind

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return getattr(self.directory.resolve(self.kind)[0], name)


class ScopedBotoClient:
    def __init__(self, directory, kind):
        self.directory = directory
        self.kind = kind

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def call(*args, **kwargs):
            client, settings = self.directory.resolve(self.kind)

            # Existing repositories use the deployment's logical bucket in Boto
            # requests. Resolve it to the selected organization's physical bucket.
            def bind(params):
                out = dict(params)
                if "Bucket" in out:
                    if out["Bucket"] not in {
                        self.directory.settings.object_store_bucket,
                        settings.object_store_bucket,
                    }:
                        raise ValueError("Bucket outside the selected organization")
                    out["Bucket"] = settings.object_store_bucket
                if isinstance(out.get("CopySource"), dict):
                    out["CopySource"] = bind(out["CopySource"])
                elif "CopySource" in out:
                    raise ValueError("A copy source must specify its scoped bucket and key")
                return out

            kwargs = bind(kwargs)
            if "Params" in kwargs:
                kwargs["Params"] = bind(kwargs["Params"])
            return getattr(client, name)(*args, **kwargs)

        return call
