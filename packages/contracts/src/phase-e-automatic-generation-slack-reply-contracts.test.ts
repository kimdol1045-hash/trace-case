import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";

import generationSlackReplySchema from "../generation/slack-reply/1.0.0/schema.json";
import packageDocument from "../package.json";
import { generationSlackReplyV1Schema } from "./index";

type JsonObject = Record<string, unknown>;

const messageId = "d9428888-122b-5c07-b3b2-2c720b88a0c1";
const messageDigest = "a".repeat(64);

function validator() {
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  addFormats(ajv);
  ajv.addSchema(generationSlackReplySchema);
  return ajv;
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

const omittedLink = { linkMode: "OMITTED", uiUrl: null } as const;
const errorLabels: Record<string, string> = {
  GENERATION_UNSUPPORTED_SCHEMA_VERSION: "지원하지 않는 결과 형식",
  GENERATION_AUTH_REJECTED: "생성 서비스 인증 거부",
  GENERATION_TIMEOUT: "생성 서비스 응답 시간 초과",
  GENERATION_TRANSPORT_UNAVAILABLE: "생성 서비스 연결 실패",
  GENERATION_RATE_LIMITED: "생성 서비스 요청 제한",
  GENERATION_PROVIDER_REJECTED: "생성 서비스 요청 거부",
  GENERATION_PROVIDER_ERROR: "생성 서비스 오류",
  GENERATION_INVALID_CONTENT_TYPE: "잘못된 응답 형식",
  GENERATION_RESPONSE_TOO_LARGE: "응답 크기 제한 초과",
  GENERATION_MALFORMED_JSON: "잘못된 JSON 응답",
  GENERATION_INVALID_RESPONSE: "검증할 수 없는 생성 결과",
};

const appliedText = [
  "✅ 자동 테스트케이스 생성이 완료되었습니다.",
  "• 새 테스트케이스: 3건",
  "• 기존 테스트케이스 재사용(변경 없음): 2건",
  "• 상위 일감: 기존 일감 재사용(변경 없음)",
].join("\n");

const appliedMessage = {
  templateVersion: "automatic-generation-slack-reply/1.0.0",
  locale: "ko-KR",
  outcome: "SUCCESS_APPLIED",
  createdCount: 3,
  reusedCount: 2,
  generatedCount: 5,
  parentOutcome: "REUSED",
  link: omittedLink,
  messageText: appliedText,
  messageDigest,
};

const metadata = {
  event_type: "tc_generation_terminal_notification_v1",
  event_payload: {
    notification_id: "generation_slack_notification_001",
    generation_job_id: "generation_job_002",
    outcome: "SUCCESS_APPLIED",
    contract_version: "1.0.0",
  },
};

const appliedIntent = {
  contractVersion: "automatic-generation-slack-reply/1.0.0",
  notificationId: "generation_slack_notification_001",
  notificationKey:
    "automatic-generation-slack-reply/1.0.0:generation_job_002:SUCCEEDED",
  workspaceId: "workspace_001",
  slackWorkItemId: "slack_work_item_001",
  slackWorkspaceId: "TQA123",
  channelId: "CQA456",
  rootMessageTs: "1787880000.000001",
  threadTs: "1787880000.000001",
  rootGenerationJobId: "generation_job_001",
  terminalGenerationJobId: "generation_job_002",
  generationTrigger: "AUTOMATIC",
  sourceVersion: 4,
  attemptNumber: 2,
  terminalStatus: "SUCCEEDED",
  outcome: "SUCCESS_APPLIED",
  deliveryDisposition: "POST",
  clientMessageId: messageId,
  metadata,
  message: appliedMessage,
};

function renderMessage(message: JsonObject) {
  const link = message.link as JsonObject | undefined;
  const linkLine =
    link?.linkMode === "ABSOLUTE_HTTPS" && typeof link.uiUrl === "string"
      ? `\n• 결과 보기: <${link.uiUrl}|QA 도구에서 보기>`
      : "";
  if (message.outcome === "SUCCESS_APPLIED") {
    const parent =
      message.parentOutcome === "CREATED"
        ? "새로 등록"
        : message.parentOutcome === "REUSED"
          ? "기존 일감 재사용(변경 없음)"
          : undefined;
    if (parent === undefined) return undefined;
    return [
      "✅ 자동 테스트케이스 생성이 완료되었습니다.",
      `• 새 테스트케이스: ${String(message.createdCount)}건`,
      `• 기존 테스트케이스 재사용(변경 없음): ${String(message.reusedCount)}건`,
      `• 상위 일감: ${parent}`,
    ].join("\n") + linkLine;
  }
  if (message.outcome === "SUCCESS_SUGGESTIONS") {
    const count = Number(message.suggestionCount);
    if (!Number.isSafeInteger(count) || count < 0) return undefined;
    return [
      "✅ 자동 테스트케이스 변경 제안이 준비되었습니다.",
      count === 0 ? "• 변경 제안: 0건(변경 없음)" : `• 변경 제안: ${count}건`,
      "• 자동 덮어쓰기: 0건",
    ].join("\n") + linkLine;
  }
  if (message.outcome === "FAILURE") {
    const code = String(message.errorCode);
    const label = errorLabels[code];
    if (label === undefined || message.errorLabel !== label) return undefined;
    return [
      "⚠️ 자동 테스트케이스 생성에 실패했습니다.",
      `• 생성 시도: ${String(message.attemptNumber)}차`,
      `• 오류 코드: ${label} (${code})`,
      `• 재시도 가능: ${message.retryable === true ? "가능" : "불가"}`,
    ].join("\n") + linkLine;
  }
  return undefined;
}

function intentSemanticsAreValid(value: unknown) {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const intent = value as JsonObject;
  const message = intent.message as JsonObject | undefined;
  const safeMetadata = intent.metadata as JsonObject | undefined;
  const eventPayload = safeMetadata?.event_payload as JsonObject | undefined;
  if (!message || !eventPayload) return false;

  const outcome = intent.outcome;
  const success = outcome === "SUCCESS_APPLIED" || outcome === "SUCCESS_SUGGESTIONS";
  const appliedCountsMatch =
    outcome !== "SUCCESS_APPLIED" ||
    Number(message.createdCount) + Number(message.reusedCount) === Number(message.generatedCount);
  return (
    intent.rootMessageTs === intent.threadTs &&
    intent.generationTrigger === "AUTOMATIC" &&
    ((success && intent.terminalStatus === "SUCCEEDED") ||
      (outcome === "FAILURE" && intent.terminalStatus === "FAILED")) &&
    message.outcome === outcome &&
    eventPayload.notification_id === intent.notificationId &&
    eventPayload.generation_job_id === intent.terminalGenerationJobId &&
    eventPayload.outcome === outcome &&
    typeof message.messageText === "string" &&
    renderMessage(message) === message.messageText &&
    typeof message.messageDigest === "string" &&
    /^[a-f0-9]{64}$/.test(message.messageDigest) &&
    appliedCountsMatch
  );
}

describe("PE-SE2-T06 automatic generation Slack reply contract", () => {
  it("compiles every strict definition and publishes the versioned package surface", () => {
    const ajv = validator();

    expect(generationSlackReplySchema.$schema).toBe(
      "https://json-schema.org/draft/2020-12/schema",
    );
    expect(ajv.getSchema(generationSlackReplySchema.$id)).toBeTypeOf("function");
    for (const name of Object.keys(generationSlackReplySchema.$defs)) {
      expect(
        ajv.getSchema(`${generationSlackReplySchema.$id}#/$defs/${name}`),
        `definition did not compile: ${name}`,
      ).toBeTypeOf("function");
    }
    expect(generationSlackReplyV1Schema).toBe(generationSlackReplySchema);
    expect(packageDocument.exports).toHaveProperty(
      "./schemas/generation/slack-reply/1.0.0",
      "./generation/slack-reply/1.0.0/schema.json",
    );
  });

  it("accepts a canonical automatic retry applied-success intent with truthful CREATED/REUSED wording", () => {
    const ajv = validator();
    const validate = ajv.getSchema(
      `${generationSlackReplySchema.$id}#/$defs/NotificationIntent`,
    );

    expect(validate?.(appliedIntent), ajv.errorsText(validate?.errors)).toBe(true);
    expect(intentSemanticsAreValid(appliedIntent)).toBe(true);
    expect(appliedText).toContain("새 테스트케이스: 3건");
    expect(appliedText).toContain("기존 테스트케이스 재사용(변경 없음): 2건");
    expect(appliedText).not.toMatch(/업데이트|덮어쓰기/);

    const mismatchedThread = clone(appliedIntent) as unknown as JsonObject;
    mismatchedThread.threadTs = "1787880000.000002";
    expect(validate?.(mismatchedThread)).toBe(true);
    expect(intentSemanticsAreValid(mismatchedThread)).toBe(false);

    const mismatchedCounts = clone(appliedIntent) as unknown as JsonObject;
    (mismatchedCounts.message as JsonObject).generatedCount = 6;
    expect(validate?.(mismatchedCounts)).toBe(true);
    expect(intentSemanticsAreValid(mismatchedCounts)).toBe(false);
  });

  it("represents suggestion success separately and fixes automatic overwrite to zero", () => {
    const ajv = validator();
    const validate = ajv.getSchema(
      `${generationSlackReplySchema.$id}#/$defs/MessageSnapshot`,
    );
    const text = [
      "✅ 자동 테스트케이스 변경 제안이 준비되었습니다.",
      "• 변경 제안: 0건(변경 없음)",
      "• 자동 덮어쓰기: 0건",
    ].join("\n");
    const suggestion = {
      templateVersion: "automatic-generation-slack-reply/1.0.0",
      locale: "ko-KR",
      outcome: "SUCCESS_SUGGESTIONS",
      suggestionCount: 0,
      automaticOverwriteCount: 0,
      link: omittedLink,
      messageText: text,
      messageDigest,
    };

    expect(validate?.(suggestion), ajv.errorsText(validate?.errors)).toBe(true);
    expect(renderMessage(suggestion)).toBe(text);
    expect(text).not.toMatch(/새 테스트케이스|재사용|업데이트|적용됨/);
    expect(validate?.({ ...suggestion, automaticOverwriteCount: 1 })).toBe(false);
    expect(validate?.({ ...suggestion, createdCount: 1 })).toBe(false);
  });

  it("allows only a fixed failure code/label and rejects raw provider or credential disclosure", () => {
    const ajv = validator();
    const validate = ajv.getSchema(
      `${generationSlackReplySchema.$id}#/$defs/MessageSnapshot`,
    );
    const text = [
      "⚠️ 자동 테스트케이스 생성에 실패했습니다.",
      "• 생성 시도: 2차",
      "• 오류 코드: 생성 서비스 응답 시간 초과 (GENERATION_TIMEOUT)",
      "• 재시도 가능: 가능",
    ].join("\n");
    const failure = {
      templateVersion: "automatic-generation-slack-reply/1.0.0",
      locale: "ko-KR",
      outcome: "FAILURE",
      attemptNumber: 2,
      errorCode: "GENERATION_TIMEOUT",
      errorLabel: "생성 서비스 응답 시간 초과",
      retryable: true,
      link: omittedLink,
      messageText: text,
      messageDigest,
    };

    expect(validate?.(failure), ajv.errorsText(validate?.errors)).toBe(true);
    expect(renderMessage(failure)).toBe(text);
    for (const unsafe of [
      { error: "xoxb-secret provider response" },
      { endpoint: "https://provider.invalid?token=secret" },
      { responseBody: { prompt: "source text" } },
      { authorization: "Bearer secret" },
    ]) {
      expect(validate?.({ ...failure, ...unsafe })).toBe(false);
    }
    expect(validate?.({ ...failure, errorCode: "UNKNOWN_PROVIDER_CODE" })).toBe(false);
    expect(renderMessage({ ...failure, errorLabel: "임의 오류" })).toBeUndefined();
  });

  it("requires a non-broadcast root-thread post with safe minimal metadata", () => {
    const ajv = validator();
    const validate = ajv.getSchema(
      `${generationSlackReplySchema.$id}#/$defs/SlackPostMessageRequest`,
    );
    const post = {
      channel: appliedIntent.channelId,
      thread_ts: appliedIntent.threadTs,
      text: appliedText,
      client_msg_id: messageId,
      reply_broadcast: false,
      unfurl_links: false,
      unfurl_media: false,
      metadata,
    };

    expect(validate?.(post), ajv.errorsText(validate?.errors)).toBe(true);
    expect(validate?.({ ...post, reply_broadcast: true })).toBe(false);
    expect(validate?.({ ...post, thread_ts: undefined })).toBe(false);
    expect(validate?.({ ...post, permalink: "https://slack.invalid/archives/C/p1" })).toBe(
      false,
    );
    const leakedMetadata = clone(post) as unknown as JsonObject;
    const leaked = leakedMetadata.metadata as JsonObject;
    (leaked.event_payload as JsonObject).workspace_id = "workspace_private";
    expect(validate?.(leakedMetadata)).toBe(false);
  });

  it("keeps delivered proof exclusive from suppressed/dead local outcomes", () => {
    const ajv = validator();
    const validate = ajv.getSchema(
      `${generationSlackReplySchema.$id}#/$defs/NotificationReceipt`,
    );
    const delivered = {
      receiptId: "generation_slack_receipt_001",
      notificationId: appliedIntent.notificationId,
      terminalOutcome: "DELIVERED",
      proof: "THREAD_RECONCILIATION",
      attemptId: "generation_slack_attempt_001",
      slackWorkspaceId: appliedIntent.slackWorkspaceId,
      channelId: appliedIntent.channelId,
      rootMessageTs: appliedIntent.rootMessageTs,
      messageTs: "1787880001.000001",
      clientMessageId: messageId,
      messageDigest: appliedMessage.messageDigest,
      recordedAt: "2026-08-28T03:00:00Z",
    };
    const dead = {
      receiptId: "generation_slack_receipt_002",
      notificationId: appliedIntent.notificationId,
      terminalOutcome: "DEAD",
      reason: "AMBIGUOUS_UNRESOLVED",
      attemptId: "generation_slack_attempt_002",
      recordedAt: "2026-08-29T03:00:00Z",
    };

    expect(validate?.(delivered), ajv.errorsText(validate?.errors)).toBe(true);
    expect(validate?.(dead), ajv.errorsText(validate?.errors)).toBe(true);
    expect(validate?.({ ...dead, messageTs: "1787880001.000001" })).toBe(false);
    expect(validate?.({ ...delivered, reason: "AMBIGUOUS_UNRESOLVED" })).toBe(false);
  });
});
