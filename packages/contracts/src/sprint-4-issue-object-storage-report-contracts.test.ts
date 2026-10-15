import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";

import coreApiSchema from "../core-api-payloads.schema.json";
import sprint1ErrorSchema from "../sprint-1/error-contract.schema.json";
import sprint2ErrorSchema from "../sprint-2/error-contract.schema.json";
import testCaseApiSchema from "../sprint-2/test-case-api.schema.json";
import cycleExecutionApiSchema from "../sprint-3/cycle-execution-api.schema.json";
import sprint3ErrorSchema from "../sprint-3/error-contract.schema.json";
import sprint4ErrorSchema from "../sprint-4/error-contract.schema.json";
import issueApiSchema from "../sprint-4/issue-api.schema.json";
import objectStorageApiSchema from "../sprint-4/object-storage-api.schema.json";
import reportingApiSchema from "../sprint-4/reporting-api.schema.json";
import stateEnumSchema from "../state-enums.schema.json";
import { sprint4ObjectStorageApiSchema } from "./index";

const schemas = [
  stateEnumSchema,
  sprint1ErrorSchema,
  sprint2ErrorSchema,
  testCaseApiSchema,
  cycleExecutionApiSchema,
  sprint3ErrorSchema,
  issueApiSchema,
  objectStorageApiSchema,
  reportingApiSchema,
  sprint4ErrorSchema,
  coreApiSchema,
];

function createValidator() {
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  addFormats(ajv);
  for (const schema of schemas) ajv.addSchema(schema);
  return ajv;
}

describe("Sprint 4 issue, private object storage, and reporting contracts", () => {
  it("compiles every schema and published definition in strict Draft 2020-12 mode", () => {
    const ajv = createValidator();
    for (const schema of [issueApiSchema, objectStorageApiSchema, reportingApiSchema, sprint4ErrorSchema]) {
      expect(ajv.getSchema(schema.$id), `schema did not compile: ${schema.$id}`).toBeTypeOf("function");
      for (const name of Object.keys(schema.$defs)) {
        expect(ajv.getSchema(`${schema.$id}#/$defs/${name}`), `definition did not compile: ${name}`).toBeTypeOf(
          "function",
        );
      }
    }
    expect(sprint4ObjectStorageApiSchema.$id).toBe(objectStorageApiSchema.$id);
  });

  it("enforces filename, MIME, size and lowercase SHA-256 upload declarations", () => {
    const validate = createValidator().getSchema(
      `${objectStorageApiSchema.$id}#/$defs/AttachmentUploadSessionRequest`,
    );
    const valid = {
      idempotencyKey: "attachment-upload-0001",
      filename: "evidence.txt",
      declaredMimeType: "text/plain",
      declaredSizeBytes: 18,
      clientSha256: "a".repeat(64),
    };
    expect(validate?.(valid)).toBe(true);
    expect(validate?.({ ...valid, filename: "../../secret.txt" })).toBe(false);
    expect(validate?.({ ...valid, declaredMimeType: "application/x-msdownload" })).toBe(false);
    expect(validate?.({ ...valid, declaredSizeBytes: 52428801 })).toBe(false);
    expect(validate?.({ ...valid, clientSha256: "A".repeat(64) })).toBe(false);
  });

  it("publishes fail-closed quarantine, jobs, observations, and legacy migration only", () => {
    const definitions = Object.keys(objectStorageApiSchema.$defs);
    expect(definitions).toEqual(
      expect.arrayContaining([
        "AttachmentUploadSessionRequest",
        "AttachmentUploadSessionResponse",
        "CompleteAttachmentUploadRequest",
        "StorageUsageObservation",
        "OrphanReconcileRequest",
        "MetadataIntegrityRequest",
        "RestoreSampleRequest",
        "LegacyAttachmentInventory",
      ]),
    );
    expect(objectStorageApiSchema.$defs.StorageUsageObservation.description).toContain("70%, 85%, 95%");
    expect(objectStorageApiSchema.$defs.ObjectStorageStatusResource.properties.scanPolicy.const).toBe(
      "FAIL_CLOSED_CLAMAV",
    );
    expect(objectStorageApiSchema.$defs.MetadataIntegrityRequest.properties.cursor).toBeDefined();
    expect(objectStorageApiSchema.$defs.LegacyAttachmentInventory.required).toContain("nextCursor");
    expect(objectStorageApiSchema.$defs.LegacyAttachmentInventory.description).toContain("omits");

    const serialized = JSON.stringify(objectStorageApiSchema);
    expect(serialized).not.toContain("driveFileId");
    expect(serialized).not.toContain("accounts.google.com");
    expect(serialized).not.toContain("googleapis.com/auth");
  });

  it("preserves typed legacy migration conflict without OAuth or quota errors", () => {
    const names = Object.keys(sprint4ErrorSchema.$defs);
    expect(names).toContain("LegacyAttachmentMigrationRequiredError");
    expect(names).not.toContain("DriveOAuthError");
    expect(names).not.toContain("DriveQuotaExceededError");
    expect(names).not.toContain("DriveOrphanReconcileError");
    expect(sprint4ErrorSchema.$defs.MalwareScanUnavailableError.description).toContain("fails closed");
  });

  it("publishes issue restore and permanent-delete payloads", () => {
    expect(Object.keys(issueApiSchema.$defs)).toEqual(
      expect.arrayContaining([
        "RestoreIssueRequest",
        "RestoreIssueResponse",
        "PermanentDeleteIssueRequest",
        "PermanentDeleteIssueResponse",
      ]),
    );
    expect(issueApiSchema.$defs.PermanentDeleteIssueRequest.description).toContain(
      "soft-deleted",
    );
  });
});
