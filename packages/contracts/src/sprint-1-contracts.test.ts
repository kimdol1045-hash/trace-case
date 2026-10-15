import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";

import coreApiSchema from "../core-api-payloads.schema.json";
import fixture from "../fixtures/sprint-1-api-contracts.json";
import auditSchema from "../sprint-1/audit.schema.json";
import errorSchema from "../sprint-1/error-contract.schema.json";
import folderMutationSchema from "../sprint-1/folder-mutation.schema.json";
import folderTreeSchema from "../sprint-1/folder-tree.schema.json";
import workspaceSchema from "../sprint-1/workspace.schema.json";

const draft202012 = "https://json-schema.org/draft/2020-12/schema";
const schemas = [workspaceSchema, folderTreeSchema, folderMutationSchema, auditSchema, errorSchema];

function createValidator() {
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  addFormats(ajv);
  for (const schema of schemas) {
    ajv.addSchema(schema);
  }
  ajv.addSchema(coreApiSchema);
  return ajv;
}

type JsonObject = Record<string, unknown>;

function validateTreeSemantics(tree: JsonObject) {
  const ids = new Set<string>();

  function visit(node: JsonObject, expectedParentId: string | null, ancestors: JsonObject[]) {
    const id = node.id as string;
    expect(ids.has(id), `duplicate folder ID: ${id}`).toBe(false);
    ids.add(id);
    expect(node.parentId).toBe(expectedParentId);
    expect(node.depth).toBe(ancestors.length);

    const expectedPath = [...ancestors, node].map((segment) => ({
      id: segment.id,
      name: segment.name,
    }));
    expect(node.path).toEqual(expectedPath);
    expect(node.subtreeTestCaseCount as number).toBeGreaterThanOrEqual(node.directTestCaseCount as number);

    for (const child of node.children as JsonObject[]) {
      visit(child, id, [...ancestors, node]);
    }
  }

  for (const root of tree.items as JsonObject[]) {
    visit(root, null, []);
  }
}

describe("Sprint 1 canonical API contracts", () => {
  it("compiles every schema in strict Draft 2020-12 mode", () => {
    const ids = new Set<string>();
    const ajv = createValidator();

    for (const schema of schemas) {
      expect(schema.$schema).toBe(draft202012);
      expect(ids.has(schema.$id), `duplicate schema ID: ${schema.$id}`).toBe(false);
      ids.add(schema.$id);
      expect(ajv.getSchema(schema.$id), `schema did not compile: ${schema.$id}`).toBeTypeOf("function");
      for (const definitionName of Object.keys(schema.$defs)) {
        const definitionRef = `${schema.$id}#/$defs/${definitionName}`;
        expect(ajv.getSchema(definitionRef), `schema definition did not compile: ${definitionRef}`).toBeTypeOf(
          "function",
        );
      }
    }
  });

  it("accepts and rejects the synthetic payload fixtures as declared", () => {
    expect(fixture.synthetic).toBe(true);
    const ajv = createValidator();

    for (const testCase of fixture.cases) {
      const validate = ajv.getSchema(testCase.schemaRef);
      expect(validate, `unknown schema ref for ${testCase.name}`).toBeTypeOf("function");
      const actual = validate?.(testCase.payload) ?? false;
      expect(actual, `${testCase.name}: ${ajv.errorsText(validate?.errors)}`).toBe(testCase.expectedValid);
    }
  });

  it("checks recursive parent, depth and breadcrumb semantics beyond JSON Schema", () => {
    const treeFixture = fixture.cases.find((testCase) => testCase.name === "recursive folder tree");
    expect(treeFixture).toBeDefined();
    validateTreeSemantics(treeFixture?.payload as JsonObject);
  });

  it("keeps the audit API read-only at the contract boundary", () => {
    const auditDefinitions = Object.keys(auditSchema.$defs);
    expect(auditDefinitions).toContain("AuditEntry");
    expect(auditDefinitions).toContain("AuditPageResponse");
    expect(auditDefinitions.some((name) => /^(Create|Update|Delete|Append)Audit/.test(name))).toBe(false);
  });

  it("routes the legacy core error definition to the strict canonical error contract", () => {
    const ajv = createValidator();
    const legacyRef = `${coreApiSchema.$id}#/$defs/ErrorResponse`;
    const validate = ajv.getSchema(legacyRef);
    expect(validate).toBeTypeOf("function");

    const validError = fixture.cases.find((testCase) => testCase.name === "version conflict error");
    const invalidError = fixture.cases.find((testCase) => testCase.name === "folder error rejects untyped detail fields");
    expect(validate?.(validError?.payload)).toBe(true);
    expect(validate?.(invalidError?.payload)).toBe(false);
  });
});
