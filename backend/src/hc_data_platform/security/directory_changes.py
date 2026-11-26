"""Commands for user-owned organizations and scoped directory administration."""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from hc_data_platform.platform_ops.projects import StableScopeId
from hc_data_platform.core.errors import problem


class OrganizationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=256)


class DirectoryChange(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    organization_id: StableScopeId
    project_id: StableScopeId | None = None
    operation: Literal["rename", "generate_code", "revoke_code", "archive", "delete"]
    name: str | None = Field(default=None, min_length=1, max_length=256)
    confirmation: str | None = None
    retention_days: Literal[0, 30] | None = None

    @model_validator(mode="after")
    def valid_operation(self):
        if (self.operation == "delete") != (self.retention_days is not None):
            raise ValueError("删除时必须选择数据保留期限")
        if (self.operation == "rename") != (self.name is not None):
            raise ValueError("重命名需要填写新名称")
        if self.project_id and self.operation in ("generate_code", "revoke_code"):
            raise ValueError("加入码仅适用于组织")
        if self.operation in ("delete", "archive") and self.confirmation != (
            self.project_id or self.organization_id
        ):
            raise ValueError("确认编号与目标不符")
        return self


class ArchivedScope(BaseModel):
    organization_id: str
    project_id: str
    display_name: str
    status: Literal["ARCHIVED", "DELETED"] = "ARCHIVED"
    purge_after: datetime | None = None
    retrying: bool = False


def unknown_scope():
    return problem(
        status=404,
        code="DIRECTORY_SCOPE_NOT_FOUND",
        title="Scope not found",
        detail="组织或项目不存在，或已停用。",
    )
