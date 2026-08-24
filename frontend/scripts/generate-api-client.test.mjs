import assert from "node:assert/strict";
import test from "node:test";

import { runtimeOperations } from "./generate-api-client.mjs";

test("runtime operation gate includes ordinary and explicit-complex OpenAPI path keys", () => {
  const operations = runtimeOperations(`openapi: 3.1.0
paths:
  /api/v1/auth/config:
    get:
      operationId: getPublicAuthConfiguration
  ? /api/v1/organizations/{organization_id}/projects/{project_id}/regions/{region_code}/stream-schemas/{schema_id}/versions/{schema_version}/dataset-references
  : get:
      operationId: listDataSchemaDatasetReferences
    post:
      operationId: associateDataSchemaDatasetReference
components: {}
`);

  assert.deepEqual(operations, [
    { method: "GET", path: "/auth/config" },
    {
      method: "GET",
      path: "/organizations/{organization_id}/projects/{project_id}/regions/{region_code}/stream-schemas/{schema_id}/versions/{schema_version}/dataset-references",
    },
    {
      method: "POST",
      path: "/organizations/{organization_id}/projects/{project_id}/regions/{region_code}/stream-schemas/{schema_id}/versions/{schema_version}/dataset-references",
    },
  ]);
});
