import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";

import coreApiSchema from "../core-api-payloads.schema.json";
import fixtureDocument from "../fixtures/sprint-5-slack-contracts.json";
import packageDocument from "../package.json";
import slackParseResultSchema from "../slack-work-item-parse-result.schema.json";
import sprint1ErrorSchema from "../sprint-1/error-contract.schema.json";
import sprint2ErrorSchema from "../sprint-2/error-contract.schema.json";
import testCaseApiSchema from "../sprint-2/test-case-api.schema.json";
import cycleExecutionApiSchema from "../sprint-3/cycle-execution-api.schema.json";
import sprint3ErrorSchema from "../sprint-3/error-contract.schema.json";
import objectStorageApiSchema from "../sprint-4/object-storage-api.schema.json";
import sprint4ErrorSchema from "../sprint-4/error-contract.schema.json";
import issueApiSchema from "../sprint-4/issue-api.schema.json";
import reportingApiSchema from "../sprint-4/reporting-api.schema.json";
import sprint5ErrorSchema from "../sprint-5/error-contract.schema.json";
import slackEventApiSchema from "../sprint-5/slack-event-api.schema.json";
import slackResourcesApiSchema from "../sprint-5/slack-resources-api.schema.json";
import stateEnumSchema from "../state-enums.schema.json";
import {
  sprint5ErrorContractSchema,
  sprint5SlackEventApiSchema,
  sprint5SlackResourcesApiSchema,
} from "./index";

const draft202012 = "https://json-schema.org/draft/2020-12/schema";
const schemas = [
  stateEnumSchema,
  slackParseResultSchema,
  sprint1ErrorSchema,
  sprint2ErrorSchema,
  testCaseApiSchema,
  cycleExecutionApiSchema,
  sprint3ErrorSchema,
  issueApiSchema,
  objectStorageApiSchema,
  reportingApiSchema,
  sprint4ErrorSchema,
  slackEventApiSchema,
  slackResourcesApiSchema,
  sprint5ErrorSchema,
  coreApiSchema,
];

type JsonObject = Record<string, unknown>;
type SemanticRule =
  | "ROOT_THREAD_IDENTITY"
  | "PRIVATE_DEFAULT_DENY"
  | "UNIQUE_ALLOWLIST_CHANNELS"
  | "EVENT_ID_REPLAY"
  | "MESSAGE_IDENTITY_DEDUP"
  | "REPLY_BEFORE_ROOT"
  | "REVISION_CHRONOLOGY"
  | "PARSE_FIELD_COMPLETENESS"
  | "QUIET_WINDOW_SCHEDULE"
  | "THREAD_SYNC_TOTALS"
  | "RETRY_STATE";
type ContractFixtureCase = {
  name: string;
  schemaRef: string;
  expectedValid: boolean;
  semanticRules?: SemanticRule[];
  payload: unknown;
};

function createValidator() {
  const ajv = new Ajv2020({allErrors: true, strict: true});
  addFormats(ajv);
  for (const schema of schemas) ajv.addSchema(schema);
  return ajv;
}

function asObject(value: unknown): JsonObject | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as JsonObject)
    : undefined;
}

function asObjects(value: unknown): JsonObject[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const objects = value.map(asObject);
  return objects.every((object) => object !== undefined) ? (objects as JsonObject[]) : undefined;
}

function rootThreadIdentity(payload: unknown) {
  const event = asObject(payload);
  return event?.action === "ROOT" && event.position === "ROOT" && event.messageTs === event.threadTs;
}

function privateDefaultDeny(payload: unknown) {
  const access = asObject(payload);
  return (
    access?.visibility === "PRIVATE" &&
    access.collectionAllowed === false &&
    access.policySource === "PRIVATE_DEFAULT_DENY"
  );
}

function uniqueAllowlistChannels(payload: unknown) {
  const request = asObject(payload);
  const channels = asObjects(request?.channels);
  if (!channels) return false;
  const ids = channels.map((channel) => channel.channelId);
  return ids.every((id) => typeof id === "string") && new Set(ids).size === ids.length;
}

function eventIdReplay(payload: unknown) {
  const proof = asObject(payload);
  const deliveries = asObjects(proof?.deliveries);
  if (!proof || !deliveries || deliveries.length !== 3) return false;
  const acks = deliveries.map((delivery) => asObject(delivery.ack));
  const eventLogIds = Array.isArray(proof.eventLogIds) ? proof.eventLogIds : [];
  const jobIds = Array.isArray(proof.jobIds) ? proof.jobIds : [];
  const workItemIds = Array.isArray(proof.workItemIds) ? proof.workItemIds : [];
  return (
    acks.every((ack) => ack !== undefined) &&
    acks[0]?.outcome === "ACCEPTED" &&
    acks[0]?.eventLogId === eventLogIds[0] &&
    Array.isArray(acks[0]?.enqueuedJobIds) &&
    acks[0]?.enqueuedJobIds[0] === jobIds[0] &&
    acks.slice(1).every(
      (ack) =>
        ack?.outcome === "DUPLICATE_EVENT_ID" &&
        ack.duplicateOfEventLogId === eventLogIds[0] &&
        Array.isArray(ack.enqueuedJobIds) &&
        ack.enqueuedJobIds.length === 0,
    ) &&
    eventLogIds.length === 1 &&
    jobIds.length === 1 &&
    workItemIds.length === 1
  );
}

function messageIdentityDedup(payload: unknown) {
  const proof = asObject(payload);
  const events = asObjects(proof?.events);
  const acks = asObjects(proof?.acks);
  if (!proof || !events || !acks || events.length !== 2 || acks.length !== 2) return false;
  const identity = (event: JsonObject) => `${String(event.workspaceId)}:${String(event.channelId)}:${String(event.messageTs)}`;
  return (
    identity(events[0] as JsonObject) === identity(events[1] as JsonObject) &&
    events[0]?.eventId !== events[1]?.eventId &&
    acks[0]?.outcome === "ACCEPTED" &&
    acks[1]?.outcome === "DUPLICATE_MESSAGE_IDENTITY" &&
    acks[1]?.duplicateOfEventLogId === acks[0]?.eventLogId &&
    Array.isArray(proof.revisionIds) &&
    proof.revisionIds.length === 1 &&
    Array.isArray(proof.jobIds) &&
    proof.jobIds.length === 1
  );
}

function replyBeforeRoot(payload: unknown) {
  const proof = asObject(payload);
  const events = asObjects(proof?.arrivalEvents);
  const messages = asObjects(proof?.messages);
  const workItem = asObject(proof?.workItem);
  const thread = asObject(proof?.thread);
  if (!events || !messages || !workItem || !thread || events.length !== 2 || messages.length !== 2) return false;
  const reply = events[0] as JsonObject;
  const root = events[1] as JsonObject;
  return (
    reply.action === "REPLY" &&
    root.action === "ROOT" &&
    reply.threadTs === root.messageTs &&
    root.threadTs === root.messageTs &&
    workItem.rootMessageTs === root.messageTs &&
    workItem.status === "ACTIVE" &&
    thread.rootMessageTs === root.messageTs &&
    thread.rootResolved === true &&
    thread.messageCount === 2 &&
    messages[0]?.position === "ROOT" &&
    messages[0]?.messageTs === root.messageTs &&
    messages[1]?.position === "REPLY" &&
    messages[1]?.messageTs === reply.messageTs &&
    messages.every((message) => message.threadId === thread.id)
  );
}

function revisionChronology(payload: unknown) {
  const proof = asObject(payload);
  const arrival = asObjects(proof?.arrivalEvents);
  const revisions = asObjects(proof?.chronologicalRevisions);
  const message = asObject(proof?.message);
  if (!arrival || !revisions || !message || arrival.length !== 2 || revisions.length < 2) return false;
  const occurredTimes = revisions.map((revision) => Date.parse(String(revision.occurredAt)));
  return (
    arrival[0]?.action === "DELETE" &&
    arrival[1]?.action === "EDIT" &&
    Date.parse(String(arrival[0]?.occurredAt)) > Date.parse(String(arrival[1]?.occurredAt)) &&
    occurredTimes.every((time, index) => index === 0 || time >= (occurredTimes[index - 1] as number)) &&
    revisions.every((revision, index) => revision.revision === index + 1 && revision.messageId === message.id) &&
    revisions.slice(1).every((revision, index) => revision.previousRevisionId === revisions[index]?.id) &&
    message.deleted === true &&
    message.currentRevisionId === revisions.at(-1)?.id &&
    revisions.at(-1)?.kind === "DELETED"
  );
}

function parseFieldCompleteness(payload: unknown) {
  const parse = asObject(payload);
  const result = asObject(parse?.result);
  if (!parse || !result || !Array.isArray(result.missingFields)) return false;
  const observedFields = ["developmentItem", "requestedMenu", "featureDescription", "requestDetails"];
  const missing = new Set(result.missingFields);
  const complementary = observedFields.every((field) => missing.has(field) !== Object.hasOwn(result, field));
  const sourcesMatch =
    Array.isArray(result.sourceMessageRevisions) &&
    Array.isArray(parse.sourceRevisionIds) &&
    [...result.sourceMessageRevisions].sort().join("|") === [...parse.sourceRevisionIds].sort().join("|");
  const questions = Array.isArray(parse.openQuestions) ? parse.openQuestions : [];
  const stateMatches =
    (missing.size === 0 && parse.status === "COMPLETE" && questions.length === 0) ||
    (missing.size > 0 && parse.status === "NEEDS_INPUT" && questions.length > 0);
  return complementary && sourcesMatch && stateMatches;
}

function quietWindowSchedule(payload: unknown) {
  const job = asObject(payload);
  if (!job || typeof job.quietWindowSeconds !== "number") return false;
  const changedAt = Date.parse(String(job.lastThreadChangedAt));
  const runAfter = Date.parse(String(job.runAfter));
  return (
    job.jobType === "QUIET_WINDOW_PARSE" &&
    job.state === "PENDING" &&
    job.attempt === 0 &&
    job.quietWindowSeconds === 600 &&
    runAfter - changedAt === job.quietWindowSeconds * 1000
  );
}

function threadSyncTotals(payload: unknown) {
  const response = asObject(payload);
  if (
    !response ||
    typeof response.messagesObserved !== "number" ||
    typeof response.revisionsInserted !== "number" ||
    typeof response.duplicateRevisions !== "number" ||
    typeof response.missingRepliesRecovered !== "number"
  ) {
    return false;
  }
  return (
    response.messagesObserved === response.revisionsInserted + response.duplicateRevisions &&
    response.missingRepliesRecovered <= response.revisionsInserted &&
    Date.parse(String(response.completedAt)) >= Date.parse(String(response.startedAt))
  );
}

function retryState(payload: unknown) {
  const job = asObject(payload);
  const retry = asObject(job?.retry);
  if (!job || !retry) return false;
  const aligned = job.attempt === retry.attempt && job.maxAttempts === retry.maxAttempts;
  if (job.state === "RETRY_WAIT") {
    const rateDelayMatches =
      retry.reason !== "RATE_LIMIT" ||
      (retry.lastHttpStatus === 429 &&
        typeof retry.retryAfterSeconds === "number" &&
        Date.parse(String(retry.nextAttemptAt)) - Date.parse(String(job.updatedAt)) === retry.retryAfterSeconds * 1000);
    return (
      aligned &&
      typeof job.attempt === "number" &&
      typeof job.maxAttempts === "number" &&
      job.attempt < job.maxAttempts &&
      job.runAfter === retry.nextAttemptAt &&
      rateDelayMatches
    );
  }
  if (job.state === "DEAD") {
    return (
      aligned &&
      job.attempt === job.maxAttempts &&
      job.runAfter === null &&
      retry.nextAttemptAt === null &&
      job.completedAt !== null
    );
  }
  return false;
}

const semanticValidators: Record<SemanticRule, (payload: unknown) => boolean> = {
  ROOT_THREAD_IDENTITY: rootThreadIdentity,
  PRIVATE_DEFAULT_DENY: privateDefaultDeny,
  UNIQUE_ALLOWLIST_CHANNELS: uniqueAllowlistChannels,
  EVENT_ID_REPLAY: eventIdReplay,
  MESSAGE_IDENTITY_DEDUP: messageIdentityDedup,
  REPLY_BEFORE_ROOT: replyBeforeRoot,
  REVISION_CHRONOLOGY: revisionChronology,
  PARSE_FIELD_COMPLETENESS: parseFieldCompleteness,
  QUIET_WINDOW_SCHEDULE: quietWindowSchedule,
  THREAD_SYNC_TOTALS: threadSyncTotals,
  RETRY_STATE: retryState,
};

function collectPropertyNames(value: unknown, names: Set<string>) {
  if (Array.isArray(value)) {
    for (const item of value) collectPropertyNames(item, names);
    return;
  }
  const object = asObject(value);
  if (!object) return;
  const properties = asObject(object.properties);
  if (properties) for (const name of Object.keys(properties)) names.add(name);
  for (const child of Object.values(object)) collectPropertyNames(child, names);
}

describe("Sprint 5 Slack public contracts", () => {
  it("compiles every Sprint 5 schema and definition in strict Draft 2020-12 mode", () => {
    const ajv = createValidator();
    const ids = new Set<string>();

    for (const schema of [slackEventApiSchema, slackResourcesApiSchema, sprint5ErrorSchema]) {
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

  it("accepts and rejects every synthetic fixture including normative ordering and dedup rules", () => {
    expect(fixtureDocument.synthetic).toBe(true);
    const ajv = createValidator();

    for (const testCase of fixtureDocument.cases as ContractFixtureCase[]) {
      const validate = ajv.getSchema(testCase.schemaRef);
      expect(validate, `unknown schema ref for ${testCase.name}`).toBeTypeOf("function");
      const schemaValid = validate?.(testCase.payload) ?? false;
      const semanticValid = (testCase.semanticRules ?? []).every((rule) => semanticValidators[rule](testCase.payload));
      expect(
        schemaValid && semanticValid,
        `${testCase.name}: ${ajv.errorsText(validate?.errors)}; semantic=${String(semanticValid)}`,
      ).toBe(testCase.expectedValid);
    }
  });

  it("publishes every ingress, access, resource, synchronization and operations wire family", () => {
    const ingressDefinitions = [
      "SlackUrlVerificationRequest",
      "SlackUrlVerificationResponse",
      "SlackHttpVerificationResult",
      "SlackEventCallbackEnvelope",
      "NormalizedRootMessageEvent",
      "NormalizedReplyMessageEvent",
      "NormalizedEditMessageEvent",
      "NormalizedDeleteMessageEvent",
      "NormalizedSlackMessageEvent",
      "SlackEventAckResponse",
    ];
    const resourceDefinitions = [
      "SlackIntegrationStatusResource",
      "GetSlackIntegrationStatusResponse",
      "SlackChannelAccessResource",
      "SlackAllowlistQuery",
      "SlackAllowlistPageResponse",
      "UpdateSlackAllowlistRequest",
      "UpdateSlackAllowlistResponse",
      "SlackEventLogResource",
      "SlackEventLogListQuery",
      "SlackEventLogPageResponse",
      "SlackWorkItemResource",
      "SlackThreadResource",
      "SlackMessageResource",
      "SlackMessageRevisionResource",
      "SlackParseResource",
      "GetSlackWorkItemResponse",
      "SlackWorkItemListQuery",
      "SlackWorkItemPageResponse",
      "ThreadSyncRequest",
      "ThreadSyncResponse",
      "QuietWindowJobResource",
      "UpsertQuietWindowJobRequest",
      "UpsertQuietWindowJobResponse",
      "CancelQuietWindowJobRequest",
      "CancelQuietWindowJobResponse",
      "RetryStateResource",
      "SlackJobResource",
      "SlackJobListQuery",
      "SlackJobPageResponse",
    ];
    expect(Object.keys(slackEventApiSchema.$defs)).toEqual(expect.arrayContaining(ingressDefinitions));
    expect(Object.keys(slackResourcesApiSchema.$defs)).toEqual(expect.arrayContaining(resourceDefinitions));
  });

  it("makes private default-deny, immutable revisions, ten-minute quiet window and retry visibility normative", () => {
    expect(slackResourcesApiSchema.$defs.SlackChannelAccessResource.description).toContain("Private channels are denied");
    expect(slackResourcesApiSchema.$defs.SlackMessageRevisionResource.description).toContain("Append-only revision");
    expect(slackResourcesApiSchema.$defs.QuietWindowJobResource.description).toContain("600 seconds");
    expect(slackResourcesApiSchema.$defs.SlackJobListQuery.description).toContain("RETRY_WAIT and DEAD");
    expect(slackEventApiSchema.$defs.DuplicateEventAck.description).toContain("neither another ledger row nor another job");
  });

  it("keeps credentials and raw authentication headers out of every public response resource and error", () => {
    const publicDefinitionNames = [
      ...Object.keys(slackEventApiSchema.$defs).filter((name) => name.endsWith("Response") || name.endsWith("Ack") || name.endsWith("Result")),
      ...Object.keys(slackResourcesApiSchema.$defs).filter(
        (name) => name.endsWith("Response") || name.endsWith("Resource") || name.endsWith("PageResponse"),
      ),
      ...Object.keys(sprint5ErrorSchema.$defs).filter((name) => name.endsWith("Error") || name === "ErrorResponse"),
    ];
    const forbidden = /^(signingSecret|botToken|appToken|authorization|authorizationHeader|headers|xSlackSignature|token)$/iu;

    for (const name of publicDefinitionNames) {
      const definition =
        slackEventApiSchema.$defs[name as keyof typeof slackEventApiSchema.$defs] ??
        slackResourcesApiSchema.$defs[name as keyof typeof slackResourcesApiSchema.$defs] ??
        sprint5ErrorSchema.$defs[name as keyof typeof sprint5ErrorSchema.$defs];
      const propertyNames = new Set<string>();
      collectPropertyNames(definition, propertyNames);
      expect([...propertyNames].filter((property) => forbidden.test(property)), `${name} leaks credential fields`).toEqual([]);
    }

    expect(slackEventApiSchema.$defs.SlackUrlVerificationRequest.properties.token.writeOnly).toBe(true);
    expect(slackEventApiSchema.$defs.SlackEventCallbackEnvelope.properties.token.writeOnly).toBe(true);
  });

  it("retains the prior cursor, idempotency, validation and optimistic concurrency errors", () => {
    const definitions = sprint5ErrorSchema.$defs;
    expect(definitions.CursorInvalidError.$ref).toContain("sprint-2/error-contract");
    expect(definitions.IdempotencyConflictError.$ref).toContain("sprint-1/error-contract");
    expect(definitions.ValidationError.$ref).toContain("sprint-1/error-contract");
    expect(definitions.VersionConflictError.$ref).toContain("sprint-1/error-contract");
  });

  it("exports all Sprint 5 schema documents from the package entrypoint", () => {
    expect(sprint5SlackEventApiSchema.$id).toBe(slackEventApiSchema.$id);
    expect(sprint5SlackResourcesApiSchema.$id).toBe(slackResourcesApiSchema.$id);
    expect(sprint5ErrorContractSchema.$id).toBe(sprint5ErrorSchema.$id);
    expect(packageDocument.exports["./schemas/sprint-5/slack-event-api"]).toBe("./sprint-5/slack-event-api.schema.json");
    expect(packageDocument.exports["./schemas/sprint-5/slack-resources-api"]).toBe(
      "./sprint-5/slack-resources-api.schema.json",
    );
    expect(packageDocument.exports["./schemas/sprint-5/error-contract"]).toBe("./sprint-5/error-contract.schema.json");
  });
});
