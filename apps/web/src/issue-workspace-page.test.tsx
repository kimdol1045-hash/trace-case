import type { CycleItem, CyclePageResponse, IssueLinkResource, IssueResource } from "@tc/api-client";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { LinkedResources } from "./issue-workspace-page";
import {
  formatIssueHistoryValue,
  issueHistoryEventLabel,
  issueHistoryFieldLabel,
  issueStatusCountQuery,
  resolveIssueLinkCycleItemId,
  shouldDisplayIssueHistoryField,
} from "./issue-presentation";

const { readFileSync } = await vi.importActual<{ readFileSync: (path: URL, encoding: "utf8") => string }>("node:fs");
const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
const issueWorkspaceSource = readFileSync(new URL("./issue-workspace-page.tsx", import.meta.url), "utf8");

function issueLink(target: IssueLinkResource["target"]): IssueLinkResource {
  return { id: "issue_link_01", issueId: "issue_01", target, createdAt: "2026-08-24T00:00:00Z", createdByLabel: "QA" };
}

describe("Issue workspace presentation", () => {
  it("turns audit tokens and structured changes into readable summaries", () => {
    expect(issueHistoryEventLabel("STATUS_CHANGED")).toBe("상태 변경");
    expect(issueHistoryEventLabel("ATTACHMENT_ADDED")).toBe("첨부파일 추가");
    expect(formatIssueHistoryValue("steps", [
      { sequence: 1, action: "결제 버튼을 누른다", expectedResult: "승인이 완료된다" },
    ])).toBe("재현 스텝 1개");
    expect(formatIssueHistoryValue("linkedCycleIds", ["cycle_01", "cycle_02"])).toBe("2개 연결");
    expect(formatIssueHistoryValue("attachmentId", "att_01HX6TKQZV")).toBe("연결됨");
    expect(formatIssueHistoryValue("assigneeLabel", null)).toBe("없음");
    expect(formatIssueHistoryValue("status", "IN_PROGRESS")).toBe("진행 중");
    expect(formatIssueHistoryValue("updatedAt", "2026-08-21T03:12:00Z")).not.toContain("T03:12:00Z");
    expect(issueHistoryFieldLabel("updatedAt")).toBe("수정 시각");
    expect(issueHistoryFieldLabel("unmappedField")).toBe("세부 정보");
    expect(shouldDisplayIssueHistoryField("resourceVersion")).toBe(false);
    expect(shouldDisplayIssueHistoryField("cycleId")).toBe(false);
    expect(shouldDisplayIssueHistoryField("linkedCycleIds")).toBe(true);
  });

  it("resolves cycle-item and execution-attempt links to a readable cycle case", () => {
    const item = {
      id: "cycle_item_01",
      testCaseId: "case_01",
      platformExecutions: [{ platform: "WEB", status: "FAIL", latestAttempt: { id: "attempt_01" } }],
    } as CycleItem;
    const cycleItemLink = issueLink({ type: "CYCLE_ITEM", cycleItemId: "cycle_item_01" });
    const attemptLink = issueLink({ type: "EXECUTION_ATTEMPT", executionAttemptId: "attempt_01" });

    expect(resolveIssueLinkCycleItemId(cycleItemLink, [item], null)).toBe("cycle_item_01");
    expect(resolveIssueLinkCycleItemId(attemptLink, [item], null)).toBe("cycle_item_01");
  });

  it("keeps every status-card total independent from the active list status", () => {
    expect(issueStatusCountQuery({ statuses: ["CLOSED"], severities: ["HIGH"], cursor: "next", limit: 25 }, "OPEN"))
      .toEqual({ statuses: ["OPEN"], severities: ["HIGH"], limit: 1 });
  });

  it("uses the immutable execution snapshot while an attempt catalog is still loading", () => {
    const link = issueLink({ type: "EXECUTION_ATTEMPT", executionAttemptId: "attempt_01" });
    const snapshot = { sourceAttemptId: "attempt_01", cycleItemId: "cycle_item_01" } as NonNullable<IssueResource["reproductionSnapshot"]>;
    expect(resolveIssueLinkCycleItemId(link, [], snapshot)).toBe("cycle_item_01");
  });

  it("prevents lingering selection on linked execution evidence without removing keyboard actions", () => {
    const link = issueLink({ type: "EXECUTION_ATTEMPT", executionAttemptId: "attempt_01" });
    const item = {
      id: "cycle_item_01",
      cycleId: "cycle_01",
      caseKey: "TC-1042",
      workItemKey: "WORK-482",
      pinnedVersion: { summary: "카드 결제 승인 결과 확인" },
      platformExecutions: [{ platform: "WEB", latestAttempt: { id: "attempt_01" } }],
    } as CycleItem;
    const cycle = { id: "cycle_01", name: "결제 회귀 테스트" } as CyclePageResponse["items"][number];
    const markup = renderToStaticMarkup(
      <MemoryRouter>
        <LinkedResources
          links={[link]}
          snapshot={null}
          cycles={[cycle]}
          items={[item]}
          pendingLinkId=""
          onAdd={() => undefined}
          onUnlink={async () => undefined}
        />
      </MemoryRouter>,
    );

    expect(markup).toContain('class="tm-linked-evidence-row is-execution-evidence"');
    expect(markup).toContain('draggable="false"');
    expect(markup).toContain('href="/test-cases/TC-1042"');
    expect(markup).toContain("연결 해제");
    expect(styles).toMatch(/\.tm-linked-evidence-row\s*\{[^}]*user-select: none/s);
    expect(styles).toContain(".tm-linked-evidence-row:hover");
    expect(styles).toContain(".tm-linked-evidence-row:active");
    expect(styles).toContain(".tm-linked-evidence-row.is-selected");
    expect(styles).toContain(".tm-linked-evidence-row:focus-within");
    expect(styles).toContain(".tm-linked-evidence-row a:focus-visible");
    expect(styles).toMatch(/@media \(max-width: 700px\)[\s\S]*?\.tm-issue-linked-list article\s*\{\s*grid-template-columns: 1fr;/);
  });

  it("keeps the issue comment editor typography scoped, readable, and responsive", () => {
    expect(issueWorkspaceSource).toContain('<textarea className="tm-comment-composer__input" aria-label="새 댓글"');
    expect(styles).not.toContain(".tm-comment-composer textarea");
    expect(styles).toMatch(/\.tm-comment-composer\s*\{[^}]*grid-template-columns: minmax\(0, 1fr\) auto;[^}]*align-items: end;/s);
    expect(styles).toMatch(/\.tm-comment-composer > \.tm-comment-composer__input\s*\{[^}]*font-size: 0\.72rem;[^}]*line-height: 1\.5;/s);
    expect(styles).toMatch(/\.tm-comment-composer > \.tm-comment-composer__input::placeholder\s*\{[^}]*color: #68706b;[^}]*opacity: 1;/s);
    expect(styles).toMatch(/\.tm-comment-composer > \.tm-comment-composer__input:focus-visible\s*\{[^}]*outline: 2px solid var\(--tm-product-focus\);[^}]*outline-offset: 2px;/s);
    expect(styles).toMatch(/@media \(max-width: 760px\)[\s\S]*?\.tm-comment-composer\s*\{[^}]*grid-template-columns: 1fr;[^}]*align-items: stretch;[^}]*\}[\s\S]*?\.tm-comment-composer > \.tm-primary-button\s*\{[^}]*width: 100%;[^}]*align-self: stretch;/s);
  });

  it("uses blob-backed image previews and consistent attachment action tokens", () => {
    expect(issueWorkspaceSource).toContain('<input ref={attachmentInputRef} hidden multiple type="file"');
    expect(issueWorkspaceSource).toContain("addPendingFiles(Array.from(event.target.files ?? []))");
    expect(issueWorkspaceSource).toContain("pendingFiles.map((file) => <article");
    expect(issueWorkspaceSource).toContain("<LocalAttachmentPreview file={file} />");
    expect(issueWorkspaceSource).toContain("<PrivateAttachmentPreview attachment={attachment} loadBlob={loadPrivateAttachmentBlob} />");
    expect(issueWorkspaceSource).not.toContain("attachment.proxyPath");
    expect(issueWorkspaceSource).toMatch(/className="tm-attachment-actions"[\s\S]*?className="tm-subtle-button tm-compact-button"[\s\S]*?className="tm-subtle-button tm-danger-button tm-compact-button"/s);
    expect(issueWorkspaceSource).toContain('aria-label={`${attachment.filename} 다운로드`}');
    expect(issueWorkspaceSource).toContain('aria-label={`${attachment.filename} 삭제`}');
    expect(styles).toMatch(/\.tm-attachment-preview\s*\{[^}]*width: 3\.5rem;[^}]*height: 3\.5rem;/s);
    expect(styles).toMatch(/\.tm-attachment-actions\s*\{[^}]*gap: 0\.4rem;/s);
    expect(styles).toContain(".tm-attachment-actions > button:focus-visible");
    expect(styles).toMatch(/\.tm-attachment-list article\.is-deleting\s*\{[^}]*opacity: 0\.68;/s);
  });

  it("requires the shared confirmation dialog before deleting an issue", () => {
    expect(issueWorkspaceSource).toContain('<DeleteConfirmDialog open={deletePending} title="이슈 삭제"');
    expect(issueWorkspaceSource).toContain('onClick={() => setDeletePending(true)}>삭제</button>');
    expect(issueWorkspaceSource).not.toContain('onClick={() => void remove()}>삭제</button>');
  });

  it("does not rewrite unchanged reproduction steps during an unrelated issue edit", () => {
    expect(issueWorkspaceSource).toContain('const originalSteps = data.issue.steps.map((step) => step.action).join("\\n").trim()');
    expect(issueWorkspaceSource).toContain('...(reproductionSteps.trim() !== originalSteps ? { steps:');
  });

  it("separates active and deleted issues and asks the API for deleted rows only", () => {
    expect(issueWorkspaceSource).toContain('role="tab" aria-selected={viewMode === "ACTIVE"}');
    expect(issueWorkspaceSource).toContain('role="tab" aria-selected={viewMode === "DELETED"}');
    expect(issueWorkspaceSource).toContain("sprint4LifecycleApi.listDeletedIssues(query, controller.signal)");
    expect(issueWorkspaceSource).toContain('aria-label={viewMode === "DELETED" ? "삭제된 이슈 목록" : "이슈 목록"}');
    expect(issueWorkspaceSource).toContain('viewMode === "DELETED" ? issue.deletedAt : issue.updatedAt');
  });

  it("keeps the existing issue form but locks mutations for a deleted issue", () => {
    expect(issueWorkspaceSource).toContain("sprint4LifecycleApi.getIssue(match.id, controller.signal)");
    expect(issueWorkspaceSource).toContain('tm-issue-detail--create${isDeleted ? " is-readonly-deleted" : ""}');
    expect(issueWorkspaceSource).toContain('disabled={isDeleted} onChange={(event) => setDraft({ ...draft, summary: event.target.value })}');
    expect(issueWorkspaceSource).toContain('readOnly={isDeleted}');
    expect(issueWorkspaceSource).toContain("sprint4LifecycleApi.restoreIssue");
    expect(issueWorkspaceSource).toContain("sprint4LifecycleApi.permanentlyDeleteIssue");
  });

  it("requires the exact issue key before permanent deletion", () => {
    expect(issueWorkspaceSource).toContain("confirmation === issueKey");
    expect(issueWorkspaceSource).not.toContain("confirmation.trim() === issueKey");
    expect(issueWorkspaceSource).toContain('aria-label="완전 삭제 확인용 이슈 키"');
    expect(issueWorkspaceSource).toContain('disabled={!confirmed || busy}');
    expect(styles).toContain(".tm-permanent-delete-button");
    expect(styles).toContain(".is-readonly-deleted input:disabled");
  });
});
