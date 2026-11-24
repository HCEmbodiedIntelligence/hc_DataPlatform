"""Tenant-scoped connection and reusable annotation guide persistence."""

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .model_inference import ModelConnection, failure


class ModelConnectionStatus(BaseModel):
    revision: int
    config: ModelConnection | None


class ConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    config: ModelConnection
    api_key: SecretStr | None = Field(default=None, min_length=1, max_length=4096)


class Guide(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=0)
    instructions: str = Field(min_length=1, max_length=12000)
    prompt: str = Field(min_length=1, max_length=12000)


def conflict():
    return failure(409, "MODEL_SETTINGS_CHANGED", "配置已更新，请刷新后重新选择")


class ProjectModelSettings:
    def __init__(self, repository, organization, project, encryption_key):
        self.db = repository
        self.scope = (organization, project)
        self.encryption_key = encryption_key

    def connection(self):
        with self.db.transaction() as cursor:
            cursor.execute(
                """SELECT revision, config FROM annotation.model_connections
                WHERE organization_id=%s AND project_id=%s""",
                self.scope,
            )
            row = cursor.fetchone()
        return {"revision": row[0], "config": row[1]} if row else {"revision": 0, "config": None}

    def credentials(self):
        with self.db.transaction() as cursor:
            cursor.execute(
                """SELECT config, pgp_sym_decrypt(api_key_ciphertext,%s)
                FROM annotation.model_connections WHERE organization_id=%s AND project_id=%s""",
                (self.encryption_key, *self.scope),
            )
            row = cursor.fetchone()
        if not row:
            raise failure(409, "MODEL_CONNECTION_MISSING", "请先在平台设置中配置当前项目的模型")
        return ModelConnection.model_validate(row[0]), row[1]

    def save_connection(self, update):
        key = update.api_key.get_secret_value() if update.api_key else None
        config = Jsonb(update.config.model_dump())
        with self.db.transaction() as cursor:
            if update.revision == 0:
                if not key:
                    raise failure(422, "MODEL_KEY_REQUIRED", "首次配置需要填写 API Key")
                cursor.execute(
                    """INSERT INTO annotation.model_connections
                    (organization_id,project_id,revision,config,api_key_ciphertext)
                    VALUES (%s,%s,1,%s,pgp_sym_encrypt(%s,%s))
                    ON CONFLICT DO NOTHING RETURNING revision""",
                    (*self.scope, config, key, self.encryption_key),
                )
            else:
                cursor.execute(
                    """SELECT config->>'endpoint' FROM annotation.model_connections
                    WHERE organization_id=%s AND project_id=%s AND revision=%s FOR UPDATE""",
                    (*self.scope, update.revision),
                )
                row = cursor.fetchone()
                if not row:
                    raise conflict()
                if row[0] != update.config.endpoint and not key:
                    raise failure(422, "MODEL_KEY_REQUIRED", "更换模型服务地址时请重新输入 API Key")
                cursor.execute(
                    """UPDATE annotation.model_connections SET revision=revision+1,
                    config=%s,
                    api_key_ciphertext=COALESCE(pgp_sym_encrypt(%s,%s),api_key_ciphertext)
                    WHERE organization_id=%s AND project_id=%s AND revision=%s
                    RETURNING revision""",
                    (config, key, self.encryption_key, *self.scope, update.revision),
                )
            saved = cursor.fetchone()
            if not saved:
                raise conflict()
        return {"revision": saved[0], "config": update.config}

    def guides(self):
        with self.db.transaction() as cursor:
            cursor.execute(
                """SELECT name,revision,instructions,prompt FROM annotation.model_annotation_guides
                WHERE organization_id=%s AND project_id=%s ORDER BY name""",
                self.scope,
            )
            return [
                Guide(name=name, revision=rev, instructions=rules, prompt=prompt)
                for name, rev, rules, prompt in cursor.fetchall()
            ]

    def get_guide(self, name, revision):
        with self.db.transaction() as cursor:
            cursor.execute(
                """SELECT instructions,prompt FROM annotation.model_annotation_guides
                WHERE organization_id=%s AND project_id=%s AND name=%s AND revision=%s""",
                (*self.scope, name, revision),
            )
            row = cursor.fetchone()
        if not row:
            raise conflict()
        return Guide(name=name, revision=revision, instructions=row[0], prompt=row[1])

    def save_guide(self, guide):
        with self.db.transaction() as cursor:
            if guide.revision == 0:
                cursor.execute(
                    """INSERT INTO annotation.model_annotation_guides
                    (organization_id,project_id,name,revision,instructions,prompt)
                    VALUES (%s,%s,%s,1,%s,%s)
                    ON CONFLICT DO NOTHING RETURNING revision""",
                    (*self.scope, guide.name, guide.instructions, guide.prompt),
                )
            else:
                cursor.execute(
                    """UPDATE annotation.model_annotation_guides
                    SET revision=revision+1,instructions=%s,prompt=%s
                    WHERE organization_id=%s AND project_id=%s AND name=%s AND revision=%s
                    RETURNING revision""",
                    (guide.instructions, guide.prompt, *self.scope, guide.name, guide.revision),
                )
            saved = cursor.fetchone()
            if not saved:
                raise conflict()
        return guide.model_copy(update={"revision": saved[0]})
