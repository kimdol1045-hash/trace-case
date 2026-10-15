import type { components, operations, paths } from "./schema.gen";

export type HealthResponse =
  paths["/api/health"]["get"]["responses"][200]["content"]["application/json"];
export type WorkspaceSettings = components["schemas"]["WorkspaceSettingsResponse"];
export type UpdateWorkspaceSettingsRequest =
  components["schemas"]["UpdateWorkspaceSettingsRequest"];
export type FolderTreeResponse = components["schemas"]["FolderTreeResponse"];
export type FolderChildrenResponse = components["schemas"]["FolderChildrenResponse"];
export type FolderMutationResponse = components["schemas"]["FolderMutationResponse"];
export type FolderDeletePreviewResponse =
  components["schemas"]["FolderDeletePreviewResponse"];
type GeneratedCreateFolderRequest = components["schemas"]["CreateFolderRequest"];
export type CreateFolderRequest = Omit<GeneratedCreateFolderRequest, "sortOrder"> & {
  sortOrder?: GeneratedCreateFolderRequest["sortOrder"];
};
export type RenameFolderRequest = components["schemas"]["RenameFolderRequest"];
export type MoveFolderRequest = components["schemas"]["MoveFolderRequest"];
export type SortFolderRequest = components["schemas"]["SortFolderRequest"];
export type SoftDeleteFolderRequest = components["schemas"]["SoftDeleteFolderRequest"];
export type RestoreFolderRequest = components["schemas"]["RestoreFolderRequest"];
export type AuditPageResponse = components["schemas"]["AuditPageResponse"];
export type ApiErrorResponse = components["schemas"]["ErrorResponse"];
export type CycleApiErrorResponse = components["schemas"]["CycleErrorResponse"];
export type AuditLogQuery = NonNullable<operations["listAuditLogs"]["parameters"]["query"]>;
export type WorkItemResource = components["schemas"]["WorkItemResourceResponse"];
export type WorkItemPageResponse = components["schemas"]["WorkItemPageResponse"];
export type WorkItemDetailResponse = components["schemas"]["WorkItemDetailResponse"];
export type CreateWorkItemRequest = components["schemas"]["CreateWorkItemRequest"];
export type UpdateWorkItemRequest = components["schemas"]["UpdateWorkItemRequest"];
export type TestCaseResource = components["schemas"]["TestCaseResourceResponse"];
export type TestCasePageResponse = components["schemas"]["TestCasePageResponse"];
export type TestCaseVersion = components["schemas"]["TestCaseVersionResponse"];
export type TestCaseVersionPageResponse = components["schemas"]["TestCaseVersionPageResponse"];
export type TestCaseVersionDiffResponse = components["schemas"]["TestCaseVersionDiffResponse"];
export type CreateTestCaseRequest = components["schemas"]["CreateTestCaseRequest"];
export type UpdateTestCaseRequest = components["schemas"]["UpdateTestCaseRequest"];
export type MoveTestCaseRequest = components["schemas"]["MoveTestCaseRequest"];
export type MoveTestCaseResponse = components["schemas"]["MoveTestCaseResponse"];
export type CopyTestCaseRequest = components["schemas"]["CopyTestCaseRequest"];
export type CopyTestCaseResponse = components["schemas"]["CopyTestCaseResponse"];
export type SoftDeleteTestCaseRequest = components["schemas"]["SoftDeleteTestCaseRequest"];
export type SoftDeleteTestCaseResponse = components["schemas"]["SoftDeleteTestCaseResponse"];
export type RestoreDeletedTestCaseRequest =
  components["schemas"]["RestoreDeletedTestCaseRequest"];
export type RestoreDeletedTestCaseResponse =
  components["schemas"]["RestoreDeletedTestCaseResponse"];
export type PermanentDeleteTestCaseRequest =
  components["schemas"]["PermanentDeleteTestCaseRequest"];
export type PermanentDeleteTestCaseResponse =
  components["schemas"]["PermanentDeleteTestCaseResponse"];
export type RestoreTestCaseVersionRequest =
  components["schemas"]["RestoreTestCaseVersionRequest"];
export type RestoreTestCaseVersionResponse =
  components["schemas"]["RestoreTestCaseVersionResponse"];
export type TestCaseListQuery = NonNullable<
  operations["listTestCases"]["parameters"]["query"]
>;
export type TestCaseVersionListQuery = NonNullable<
  operations["listTestCaseVersions"]["parameters"]["query"]
>;
export type TestCaseVersionDiffQuery = NonNullable<
  operations["diffTestCaseVersions"]["parameters"]["query"]
>;
export type CycleResource = components["schemas"]["CycleResourceResponse"];
export type CyclePageResponse = components["schemas"]["CyclePageResponse"];
export type CycleItem = components["schemas"]["CycleItemResponse"];
export type CycleItemPageResponse = components["schemas"]["CycleItemPageResponse"];
export type ScopeOptionPageResponse = components["schemas"]["ScopeOptionPageResponse"];
export type ScopePreviewResponse = components["schemas"]["ScopePreviewResponse"];
export type ExecutionAttempt = components["schemas"]["ExecutionAttemptResponse"];
export type ExecutionAttemptHistoryPageResponse =
  components["schemas"]["ExecutionAttemptHistoryPageResponse"];
export type StepExecution = components["schemas"]["StepExecutionResponse"];
export type RecordStepResultsRequest = components["schemas"]["RecordStepResultsRequest"];
export type CycleProgressResponse = components["schemas"]["CycleProgressResponse"];
export type OverallProgress = components["schemas"]["OverallProgressResponse"];
export type PlatformProgress = components["schemas"]["PlatformProgressResponse"];
export type OwnerProgress = components["schemas"]["OwnerProgressResponse"];
export type WorkItemProgress = components["schemas"]["WorkItemProgressResponse"];
export type LabelProgress = components["schemas"]["LabelProgressResponse"];
export type CreateCycleRequest = components["schemas"]["CreateCycleRequest"];
export type UpdateCycleRequest = components["schemas"]["UpdateCycleRequest"];
export type UpdateCycleScopeRequest = components["schemas"]["UpdateCycleScopeRequest"];
export type DeleteCycleRequest = components["schemas"]["DeleteCycleRequest"];
export type DeleteCycleResponse = components["schemas"]["DeleteCycleResponse"];
export type RestoreCycleRequest = components["schemas"]["RestoreCycleRequest"];
export type RestoreCycleResponse = components["schemas"]["RestoreCycleResponse"];
export type PermanentDeleteCycleRequest =
  components["schemas"]["PermanentDeleteCycleRequest"];
export type PermanentDeleteCycleResponse =
  components["schemas"]["PermanentDeleteCycleResponse"];
export type StartCycleRequest = components["schemas"]["StartCycleRequest"];
export type StartCycleResponse = components["schemas"]["StartCycleResponse"];
export type CompleteCycleRequest = components["schemas"]["CompleteCycleRequest"];
export type CompleteCycleResponse = components["schemas"]["CompleteCycleResponse"];
export type ReopenCycleRequest = components["schemas"]["ReopenCycleRequest"];
export type ReopenCycleResponse = components["schemas"]["ReopenCycleResponse"];
export type RefreshCycleSnapshotRequest = components["schemas"]["RefreshCycleSnapshotRequest"];
export type RefreshCycleItemSnapshotRequest =
  components["schemas"]["RefreshCycleItemSnapshotRequest"];
export type RefreshCycleItemSnapshotResponse =
  components["schemas"]["RefreshCycleItemSnapshotResponse"];
export type StartExecutionAttemptRequest =
  components["schemas"]["StartExecutionAttemptRequest"];
export type CompleteExecutionAttemptRequest =
  components["schemas"]["CompleteExecutionAttemptRequest"];
export type RetryExecutionAttemptRequest =
  components["schemas"]["RetryExecutionAttemptRequest"];
export type RetryExecutionAttemptResponse =
  components["schemas"]["RetryExecutionAttemptResponse"];
export type CycleListQuery = NonNullable<operations["listCycles"]["parameters"]["query"]>;
export type CycleScopeOptionQuery = operations["listCycleScopeOptions"]["parameters"]["query"];
export type CycleScopePreviewQuery = operations["previewCycleScope"]["parameters"]["query"];
export type CycleItemListQuery = NonNullable<
  operations["listCycleItems"]["parameters"]["query"]
>;
export type ExecutionAttemptHistoryQuery = NonNullable<
  operations["listExecutionAttemptHistory"]["parameters"]["query"]
>;
export type CycleProgressQuery = NonNullable<
  operations["getCycleProgress"]["parameters"]["query"]
>;
export type IssueResource = components["schemas"]["IssueResource"];
export type IssuePage = components["schemas"]["IssuePage"];
export type CreateIssueRequest = components["schemas"]["CreateIssueRequest"];
export type CreateIssueFromAttemptRequest =
  components["schemas"]["CreateIssueFromAttemptRequest"];
export type UpdateIssueRequest = components["schemas"]["UpdateIssueRequest"];
export type DeleteIssueRequest = components["schemas"]["DeleteIssueRequest"];
export type DeleteIssueResponse = components["schemas"]["DeleteIssueResponse"];
export type RestoreIssueRequest = components["schemas"]["RestoreIssueRequest"];
export type RestoreIssueResponse = components["schemas"]["RestoreIssueResponse"];
export type PermanentDeleteIssueRequest =
  components["schemas"]["PermanentDeleteIssueRequest"];
export type PermanentDeleteIssueResponse =
  components["schemas"]["PermanentDeleteIssueResponse"];
export type IssueLinkResource = components["schemas"]["IssueLinkResource"];
export type IssueLinkPage = components["schemas"]["IssueLinkPage"];
export type CreateIssueLinkRequest = components["schemas"]["CreateIssueLinkRequest"];
export type DeleteIssueLinkRequest = components["schemas"]["DeleteIssueLinkRequest"];
export type DeleteIssueLinkResponse = components["schemas"]["DeleteIssueLinkResponse"];
export type IssueCommentResource = components["schemas"]["IssueCommentResource"];
export type IssueCommentPage = components["schemas"]["IssueCommentPage"];
export type CreateIssueCommentRequest =
  components["schemas"]["CreateIssueCommentRequest"];
export type IssueHistoryPage = components["schemas"]["IssueHistoryPage"];
export type IssueListQuery = NonNullable<operations["listIssues"]["parameters"]["query"]>;
export type PageQuery = {
  cursor?: string | null;
  limit?: number;
  includeDeleted?: boolean;
};
export type AttachmentResource = components["schemas"]["AttachmentResource"];
export type AttachmentPage = components["schemas"]["AttachmentPage"];
export type AttachmentUploadResponse = components["schemas"]["AttachmentUploadResponse"];
export type AttachmentUploadSessionRequest =
  components["schemas"]["AttachmentUploadSessionRequest"];
export type AttachmentUploadSessionResponse =
  components["schemas"]["AttachmentUploadSessionResponse"];
export type CompleteAttachmentUploadRequest =
  components["schemas"]["CompleteAttachmentUploadRequest"];
export type ObjectStorageStatus = components["schemas"]["ObjectStorageStatusResource"];
export type StorageUsageObservation = components["schemas"]["StorageUsageObservation"];
export type MetadataIntegrityRequest = components["schemas"]["MetadataIntegrityRequest"];
export type MetadataIntegrityResponse = components["schemas"]["MetadataIntegrityResponse"];
export type RestoreSampleRequest = components["schemas"]["RestoreSampleRequest"];
export type RestoreSampleResponse = components["schemas"]["RestoreSampleResponse"];
export type LegacyAttachmentInventory = components["schemas"]["LegacyAttachmentInventory"];
export type LegacyAttachmentInventoryQuery = NonNullable<
  operations["getLegacyAttachmentInventory"]["parameters"]["query"]
>;
export type SoftDeleteAttachmentRequest =
  components["schemas"]["SoftDeleteAttachmentRequest"];
export type SoftDeleteAttachmentResponse =
  components["schemas"]["SoftDeleteAttachmentResponse"];
export type OrphanReconcileRequest = components["schemas"]["OrphanReconcileRequest"];
export type OrphanReconcileResponse = components["schemas"]["OrphanReconcileResponse"];
export type OverviewKpi = components["schemas"]["OverviewKpiResponse"];
export type CycleReportPage = components["schemas"]["CycleReportPageResponse"];
export type OverviewKpiQuery = NonNullable<
  operations["getOverviewKpis"]["parameters"]["query"]
>;
export type CycleReportQuery = NonNullable<
  operations["listCycleReports"]["parameters"]["query"]
>;
export type AttachmentListQuery = NonNullable<
  operations["listAttachments"]["parameters"]["query"]
>;
export type SlackIntegrationStatus = components["schemas"]["SlackIntegrationStatusResource"];
export type SlackAllowlistPage = components["schemas"]["SlackAllowlistPageResponse"];
export type UpdateSlackAllowlistRequest = components["schemas"]["UpdateSlackAllowlistRequest"];
export type UpdateSlackAllowlistResponse = components["schemas"]["UpdateSlackAllowlistResponse"];
export type SlackWorkItemPage = components["schemas"]["SlackWorkItemPageResponse"];
export type SlackWorkItemDetail = components["schemas"]["GetSlackWorkItemResponse"];
export type SlackEventLogPage = components["schemas"]["SlackEventLogPageResponse"];
export type SlackJobPage = components["schemas"]["SlackJobPageResponse"];
export type ThreadSyncRequest = components["schemas"]["ThreadSyncRequest"];
export type ThreadSyncResponse = components["schemas"]["ThreadSyncResponse"];
export type UpsertQuietWindowJobRequest =
  components["schemas"]["UpsertQuietWindowJobRequest"];
export type UpsertQuietWindowJobResponse =
  components["schemas"]["UpsertQuietWindowJobResponse"];
export type CancelQuietWindowJobRequest =
  components["schemas"]["CancelQuietWindowJobRequest"];
export type CancelQuietWindowJobResponse =
  components["schemas"]["CancelQuietWindowJobResponse"];
export type SlackAllowlistQuery = NonNullable<
  operations["listSlackAllowlist"]["parameters"]["query"]
>;
export type SlackWorkItemQuery = NonNullable<
  operations["listSlackWorkItems"]["parameters"]["query"]
>;
export type SlackEventLogQuery = NonNullable<
  operations["listSlackEvents"]["parameters"]["query"]
>;
export type SlackJobQuery = NonNullable<
  operations["listSlackJobs"]["parameters"]["query"]
>;
export type KnowledgeNodeResource = components["schemas"]["KnowledgeNodeResource"];
export type KnowledgeSourceRef = components["schemas"]["KnowledgeSourceRef"];
export type KnowledgeCitation = components["schemas"]["KnowledgeCitation"];
export type KnowledgeNodePage = components["schemas"]["KnowledgeNodePage"];
export type KnowledgeNodeDetail = components["schemas"]["KnowledgeNodeDetail"];
export type KnowledgeEvidencePage = components["schemas"]["KnowledgeEvidencePage"];
export type KnowledgeStatusPatchRequest =
  components["schemas"]["KnowledgeStatusPatchRequest"];
export type KnowledgeStatusPatchResponse =
  components["schemas"]["KnowledgeStatusPatchResponse"];
export type KnowledgeStatusAuditPage = components["schemas"]["KnowledgeStatusAuditPage"];
export type HybridSearchRequest = components["schemas"]["HybridSearchRequest"];
export type HybridSearchResponse = components["schemas"]["HybridSearchResponse"];
export type OntologyExplorerRequest = components["schemas"]["OntologyExplorerRequest"];
export type OntologyExplorerResponse = components["schemas"]["OntologyExplorerResponse"];
export type KnowledgeProjectionJobRequest =
  components["schemas"]["KnowledgeProjectionJobRequest"];
export type KnowledgeReindexJobRequest =
  components["schemas"]["KnowledgeReindexJobRequest"];
export type KnowledgeJobResponse = components["schemas"]["KnowledgeJobResponse"];
export type KnowledgeNodeListQuery = NonNullable<
  operations["listKnowledgeNodes"]["parameters"]["query"]
>;
export type GenerationQualityCandidate =
  components["schemas"]["GenerationQualityCandidateResource"];
export type GenerationQualityCandidatePage =
  components["schemas"]["GenerationQualityCandidatePage"];
export type ImportGenerationQualityCandidateRequest =
  components["schemas"]["ImportGenerationQualityCandidateRequest"];
export type CreateGenerationQualityDecisionRequest =
  components["schemas"]["CreateGenerationQualityDecisionRequest"];
export type GenerationQualityReviewedSnapshot =
  components["schemas"]["GenerationQualityReviewedSnapshotResource"];
export type GenerationQualityCandidateQuery = NonNullable<
  operations["listGenerationQualityReviewCandidates"]["parameters"]["query"]
>;

export interface AttachmentUploadMetadata {
  idempotencyKey: string;
  owner:
    | { type: "ISSUE"; issueId: string }
    | { type: "EXECUTION_ATTEMPT"; executionAttemptId: string };
  filename: string;
  declaredMimeType: AttachmentResource["mimeType"];
  declaredSizeBytes: number;
  clientSha256?: string;
}

export interface RequestOptions {
  signal?: AbortSignal;
  idempotencyKey?: string;
  traceId?: string;
  actorLabel?: string;
}

export interface QualityReviewRequestOptions {
  reviewerToken: string;
  signal?: AbortSignal;
  idempotencyKey?: string;
  traceId?: string;
}

export class ApiClientError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly response?: ApiErrorResponse | CycleApiErrorResponse,
  ) {
    super(message);
    this.name = "ApiClientError";
  }
}

export function createApiClient(baseUrl: string) {
  const normalizedBaseUrl = baseUrl.replace(/\/$/, "");

  return {
    async getHealth(signal?: AbortSignal): Promise<HealthResponse> {
      return requestJson<HealthResponse>(
        `${normalizedBaseUrl}/api/health`,
        { signal },
        "API health request failed",
      );
    },

    async importGenerationQualityReviewCandidate(
      payload: ImportGenerationQualityCandidateRequest,
      options: RequestOptions = {},
    ): Promise<GenerationQualityCandidate> {
      return requestJson<GenerationQualityCandidate>(
        `${normalizedBaseUrl}/api/generation-quality/review-candidates`,
        jsonRequest("POST", payload, withBodyIdempotency(payload, options)),
        "Generation quality candidate import failed",
      );
    },

    async listGenerationQualityReviewCandidates(
      query: GenerationQualityCandidateQuery = {},
      signal?: AbortSignal,
    ): Promise<GenerationQualityCandidatePage> {
      return requestJson<GenerationQualityCandidatePage>(
        `${normalizedBaseUrl}/api/generation-quality/review-candidates${queryString(query)}`,
        { signal },
        "Generation quality candidate list failed",
      );
    },

    async getGenerationQualityReviewCandidate(
      candidateId: string,
      signal?: AbortSignal,
    ): Promise<GenerationQualityCandidate> {
      return requestJson<GenerationQualityCandidate>(
        generationQualityCandidateUrl(normalizedBaseUrl, candidateId),
        { signal },
        "Generation quality candidate detail failed",
      );
    },

    async createGenerationQualityReviewDecision(
      candidateId: string,
      payload: CreateGenerationQualityDecisionRequest,
      options: QualityReviewRequestOptions,
    ): Promise<GenerationQualityCandidate> {
      return requestJson<GenerationQualityCandidate>(
        `${generationQualityCandidateUrl(normalizedBaseUrl, candidateId)}/decisions`,
        qualityReviewJsonRequest(
          payload,
          withBodyIdempotency(payload, options),
          options.reviewerToken,
        ),
        "Generation quality review decision failed",
      );
    },

    async getGenerationQualityReviewedSnapshot(
      candidateId: string,
      signal?: AbortSignal,
    ): Promise<GenerationQualityReviewedSnapshot> {
      return requestJson<GenerationQualityReviewedSnapshot>(
        `${generationQualityCandidateUrl(normalizedBaseUrl, candidateId)}/reviewed-snapshot`,
        { signal },
        "Generation quality reviewed snapshot request failed",
      );
    },

    async getWorkspaceSettings(signal?: AbortSignal): Promise<WorkspaceSettings> {
      return requestJson<WorkspaceSettings>(
        `${normalizedBaseUrl}/api/workspace/settings`,
        { signal },
        "Workspace settings request failed",
      );
    },

    async updateWorkspaceSettings(
      payload: UpdateWorkspaceSettingsRequest,
      options: RequestOptions = {},
    ): Promise<WorkspaceSettings> {
      return requestJson<WorkspaceSettings>(
        `${normalizedBaseUrl}/api/workspace/settings`,
        jsonRequest("PATCH", payload, options),
        "Workspace settings update failed",
      );
    },

    async getSlackIntegrationStatus(signal?: AbortSignal): Promise<SlackIntegrationStatus> {
      return requestJson<SlackIntegrationStatus>(
        `${normalizedBaseUrl}/api/integrations/slack/status`,
        { signal },
        "Slack integration status request failed",
      );
    },

    async listSlackAllowlist(
      query: SlackAllowlistQuery = {},
      signal?: AbortSignal,
    ): Promise<SlackAllowlistPage> {
      return requestJson<SlackAllowlistPage>(
        `${normalizedBaseUrl}/api/integrations/slack/allowlist${queryString(query)}`,
        { signal },
        "Slack allowlist request failed",
      );
    },

    async updateSlackAllowlist(
      payload: UpdateSlackAllowlistRequest,
      options: RequestOptions = {},
    ): Promise<UpdateSlackAllowlistResponse> {
      return requestJson<UpdateSlackAllowlistResponse>(
        `${normalizedBaseUrl}/api/integrations/slack/allowlist`,
        jsonRequest("PUT", payload, withBodyIdempotency(payload, options)),
        "Slack allowlist update failed",
      );
    },

    async listSlackWorkItems(
      query: SlackWorkItemQuery = {},
      signal?: AbortSignal,
    ): Promise<SlackWorkItemPage> {
      return requestJson<SlackWorkItemPage>(
        `${normalizedBaseUrl}/api/slack-work-items${queryString(query)}`,
        { signal },
        "Slack work item request failed",
      );
    },

    async getSlackWorkItem(
      workItemId: string,
      signal?: AbortSignal,
    ): Promise<SlackWorkItemDetail> {
      return requestJson<SlackWorkItemDetail>(
        `${normalizedBaseUrl}/api/slack-work-items/${encodeURIComponent(workItemId)}`,
        { signal },
        "Slack work item detail request failed",
      );
    },

    async listSlackEvents(
      query: SlackEventLogQuery = {},
      signal?: AbortSignal,
    ): Promise<SlackEventLogPage> {
      return requestJson<SlackEventLogPage>(
        `${normalizedBaseUrl}/api/slack-events${queryString(query)}`,
        { signal },
        "Slack event log request failed",
      );
    },

    async syncSlackThread(
      workItemId: string,
      payload: ThreadSyncRequest,
      options: RequestOptions = {},
    ): Promise<ThreadSyncResponse> {
      return requestJson<ThreadSyncResponse>(
        `${normalizedBaseUrl}/api/slack-work-items/${encodeURIComponent(workItemId)}/sync-thread`,
        jsonRequest("POST", payload, withBodyIdempotency(payload, options)),
        "Slack thread sync request failed",
      );
    },

    async upsertSlackQuietWindow(
      workItemId: string,
      payload: UpsertQuietWindowJobRequest,
      options: RequestOptions = {},
    ): Promise<UpsertQuietWindowJobResponse> {
      return requestJson<UpsertQuietWindowJobResponse>(
        `${normalizedBaseUrl}/api/slack-work-items/${encodeURIComponent(workItemId)}/quiet-window`,
        jsonRequest("POST", payload, withBodyIdempotency(payload, options)),
        "Slack quiet-window update failed",
      );
    },

    async cancelSlackQuietWindow(
      jobId: string,
      payload: CancelQuietWindowJobRequest,
      options: RequestOptions = {},
    ): Promise<CancelQuietWindowJobResponse> {
      return requestJson<CancelQuietWindowJobResponse>(
        `${normalizedBaseUrl}/api/slack-jobs/${encodeURIComponent(jobId)}/cancel`,
        jsonRequest("POST", payload, withBodyIdempotency(payload, options)),
        "Slack quiet-window cancellation failed",
      );
    },

    async listSlackJobs(
      query: SlackJobQuery = {},
      signal?: AbortSignal,
    ): Promise<SlackJobPage> {
      return requestJson<SlackJobPage>(
        `${normalizedBaseUrl}/api/slack-jobs${queryString(query)}`,
        { signal },
        "Slack job request failed",
      );
    },

    async listAuditLogs(
      query: AuditLogQuery = {},
      signal?: AbortSignal,
    ): Promise<AuditPageResponse> {
      const search = queryString(query);
      return requestJson<AuditPageResponse>(
        `${normalizedBaseUrl}/api/audit-logs${search}`,
        { signal },
        "Audit log request failed",
      );
    },

    async listTestCases(
      query: TestCaseListQuery = {},
      signal?: AbortSignal,
    ): Promise<TestCasePageResponse> {
      return requestJson<TestCasePageResponse>(
        `${normalizedBaseUrl}/api/test-cases${queryString(query)}`,
        { signal },
        "Test case list request failed",
      );
    },

    async listWorkItems(
      limit = 200,
      offset = 0,
      signal?: AbortSignal,
    ): Promise<WorkItemPageResponse> {
      return requestJson<WorkItemPageResponse>(
        `${normalizedBaseUrl}/api/work-items${queryString({ limit, offset })}`,
        { signal },
        "Work item list request failed",
      );
    },

    async createWorkItem(
      payload: CreateWorkItemRequest,
      options: RequestOptions = {},
    ): Promise<WorkItemResource> {
      return requestJson<WorkItemResource>(
        `${normalizedBaseUrl}/api/work-items`,
        jsonRequest("POST", payload, options),
        "Work item create failed",
      );
    },

    async getWorkItem(
      workItemKey: string,
      signal?: AbortSignal,
    ): Promise<WorkItemDetailResponse> {
      return requestJson<WorkItemDetailResponse>(
        `${normalizedBaseUrl}/api/work-items/${encodeURIComponent(workItemKey)}`,
        { signal },
        "Work item request failed",
      );
    },

    async updateWorkItem(
      workItemKey: string,
      payload: UpdateWorkItemRequest,
      options: RequestOptions = {},
    ): Promise<WorkItemResource> {
      return requestJson<WorkItemResource>(
        `${normalizedBaseUrl}/api/work-items/${encodeURIComponent(workItemKey)}`,
        jsonRequest("PATCH", payload, options),
        "Work item update failed",
      );
    },

    async createTestCase(
      payload: CreateTestCaseRequest,
      options: RequestOptions = {},
    ): Promise<TestCaseResource> {
      return requestJson<TestCaseResource>(
        `${normalizedBaseUrl}/api/test-cases`,
        jsonRequest("POST", payload, options),
        "Test case create failed",
      );
    },

    async getTestCase(
      caseId: string,
      signal?: AbortSignal,
      includeDeleted = false,
    ): Promise<TestCaseResource> {
      return requestJson<TestCaseResource>(
        `${caseUrl(normalizedBaseUrl, caseId)}${queryString(includeDeleted ? { includeDeleted } : {})}`,
        { signal },
        "Test case request failed",
      );
    },

    async updateTestCase(
      caseId: string,
      payload: UpdateTestCaseRequest,
      options: RequestOptions = {},
    ): Promise<TestCaseResource> {
      return requestJson<TestCaseResource>(
        caseUrl(normalizedBaseUrl, caseId),
        jsonRequest("PATCH", payload, options),
        "Test case update failed",
      );
    },

    async listTestCaseVersions(
      caseId: string,
      query: TestCaseVersionListQuery = {},
      signal?: AbortSignal,
    ): Promise<TestCaseVersionPageResponse> {
      return requestJson<TestCaseVersionPageResponse>(
        `${caseUrl(normalizedBaseUrl, caseId)}/versions${queryString(query)}`,
        { signal },
        "Test case version list failed",
      );
    },

    async getTestCaseVersion(
      caseId: string,
      version: number,
      signal?: AbortSignal,
    ): Promise<TestCaseVersion> {
      return requestJson<TestCaseVersion>(
        `${caseUrl(normalizedBaseUrl, caseId)}/versions/${encodeURIComponent(String(version))}`,
        { signal },
        "Test case version request failed",
      );
    },

    async diffTestCaseVersions(
      caseId: string,
      query: TestCaseVersionDiffQuery,
      signal?: AbortSignal,
    ): Promise<TestCaseVersionDiffResponse> {
      return requestJson<TestCaseVersionDiffResponse>(
        `${caseUrl(normalizedBaseUrl, caseId)}/versions/diff${queryString(query)}`,
        { signal },
        "Test case version diff failed",
      );
    },

    async restoreTestCaseVersion(
      caseId: string,
      version: number,
      payload: RestoreTestCaseVersionRequest,
      options: RequestOptions = {},
    ): Promise<RestoreTestCaseVersionResponse> {
      return requestJson<RestoreTestCaseVersionResponse>(
        `${caseUrl(normalizedBaseUrl, caseId)}/restore/${encodeURIComponent(String(version))}`,
        jsonRequest("POST", payload, options),
        "Test case version restore failed",
      );
    },

    async moveTestCase(
      caseId: string,
      payload: MoveTestCaseRequest,
      options: RequestOptions = {},
    ): Promise<MoveTestCaseResponse> {
      return requestJson<MoveTestCaseResponse>(
        `${caseUrl(normalizedBaseUrl, caseId)}/move`,
        jsonRequest("POST", payload, options),
        "Test case move failed",
      );
    },

    async copyTestCase(
      caseId: string,
      payload: CopyTestCaseRequest,
      options: RequestOptions = {},
    ): Promise<CopyTestCaseResponse> {
      return requestJson<CopyTestCaseResponse>(
        `${caseUrl(normalizedBaseUrl, caseId)}/copy`,
        jsonRequest("POST", payload, options),
        "Test case copy failed",
      );
    },

    async softDeleteTestCase(
      caseId: string,
      payload: SoftDeleteTestCaseRequest,
      options: RequestOptions = {},
    ): Promise<SoftDeleteTestCaseResponse> {
      return requestJson<SoftDeleteTestCaseResponse>(
        caseUrl(normalizedBaseUrl, caseId),
        jsonRequest("DELETE", payload, options),
        "Test case delete failed",
      );
    },

    async restoreDeletedTestCase(
      caseId: string,
      payload: RestoreDeletedTestCaseRequest,
      options: RequestOptions = {},
    ): Promise<RestoreDeletedTestCaseResponse> {
      return requestJson<RestoreDeletedTestCaseResponse>(
        `${caseUrl(normalizedBaseUrl, caseId)}/restore`,
        jsonRequest("POST", payload, options),
        "Deleted test case restore failed",
      );
    },

    async permanentlyDeleteTestCase(
      caseId: string,
      payload: PermanentDeleteTestCaseRequest,
      options: RequestOptions = {},
    ): Promise<PermanentDeleteTestCaseResponse> {
      return requestJson<PermanentDeleteTestCaseResponse>(
        `${caseUrl(normalizedBaseUrl, caseId)}/permanent`,
        jsonRequest("DELETE", payload, options),
        "Test case permanent delete failed",
      );
    },

    async listCycles(
      query: CycleListQuery = {},
      signal?: AbortSignal,
    ): Promise<CyclePageResponse> {
      return requestJson<CyclePageResponse>(
        `${normalizedBaseUrl}/api/cycles${queryString(query)}`,
        { signal },
        "Cycle list request failed",
      );
    },

    async listCycleScopeOptions(
      query: CycleScopeOptionQuery,
      signal?: AbortSignal,
    ): Promise<ScopeOptionPageResponse> {
      return requestJson<ScopeOptionPageResponse>(
        `${normalizedBaseUrl}/api/cycles/scope-options${queryString(query)}`,
        { signal },
        "Cycle scope option request failed",
      );
    },

    async previewCycleScope(
      query: CycleScopePreviewQuery,
      signal?: AbortSignal,
    ): Promise<ScopePreviewResponse> {
      return requestJson<ScopePreviewResponse>(
        `${normalizedBaseUrl}/api/cycles/scope-preview${queryString(query)}`,
        { signal },
        "Cycle scope preview failed",
      );
    },

    async createCycle(
      payload: CreateCycleRequest,
      options: RequestOptions = {},
    ): Promise<CycleResource> {
      return requestJson<CycleResource>(
        `${normalizedBaseUrl}/api/cycles`,
        jsonRequest("POST", payload, options),
        "Cycle create failed",
      );
    },

    async getCycle(
      cycleId: string,
      signal?: AbortSignal,
      includeDeleted = false,
    ): Promise<CycleResource> {
      return requestJson<CycleResource>(
        `${cycleUrl(normalizedBaseUrl, cycleId)}${queryString(includeDeleted ? { includeDeleted } : {})}`,
        { signal },
        "Cycle request failed",
      );
    },

    async updateCycle(
      cycleId: string,
      payload: UpdateCycleRequest,
      options: RequestOptions = {},
    ): Promise<CycleResource> {
      return requestJson<CycleResource>(
        cycleUrl(normalizedBaseUrl, cycleId),
        jsonRequest("PATCH", payload, options),
        "Cycle update failed",
      );
    },

    async updateCycleScope(
      cycleId: string,
      payload: UpdateCycleScopeRequest,
      options: RequestOptions = {},
    ): Promise<CycleResource> {
      return requestJson<CycleResource>(
        `${cycleUrl(normalizedBaseUrl, cycleId)}/scope`,
        jsonRequest("PATCH", payload, options),
        "Cycle scope update failed",
      );
    },

    async deleteCycle(
      cycleId: string,
      payload: DeleteCycleRequest,
      options: RequestOptions = {},
    ): Promise<DeleteCycleResponse> {
      return requestJson<DeleteCycleResponse>(
        cycleUrl(normalizedBaseUrl, cycleId),
        jsonRequest("DELETE", payload, options),
        "Cycle delete failed",
      );
    },

    async restoreCycle(
      cycleId: string,
      payload: RestoreCycleRequest,
      options: RequestOptions = {},
    ): Promise<RestoreCycleResponse> {
      return requestJson<RestoreCycleResponse>(
        `${cycleUrl(normalizedBaseUrl, cycleId)}/restore`,
        jsonRequest("POST", payload, options),
        "Cycle restore failed",
      );
    },

    async permanentlyDeleteCycle(
      cycleId: string,
      payload: PermanentDeleteCycleRequest,
      options: RequestOptions = {},
    ): Promise<PermanentDeleteCycleResponse> {
      return requestJson<PermanentDeleteCycleResponse>(
        `${cycleUrl(normalizedBaseUrl, cycleId)}/permanent`,
        jsonRequest("DELETE", payload, options),
        "Cycle permanent delete failed",
      );
    },

    async startCycle(
      cycleId: string,
      payload: StartCycleRequest,
      options: RequestOptions = {},
    ): Promise<StartCycleResponse> {
      return requestJson<StartCycleResponse>(
        `${cycleUrl(normalizedBaseUrl, cycleId)}/start`,
        jsonRequest("POST", payload, options),
        "Cycle start failed",
      );
    },

    async completeCycle(
      cycleId: string,
      payload: CompleteCycleRequest,
      options: RequestOptions = {},
    ): Promise<CompleteCycleResponse> {
      return requestJson<CompleteCycleResponse>(
        `${cycleUrl(normalizedBaseUrl, cycleId)}/complete`,
        jsonRequest("POST", payload, options),
        "Cycle complete failed",
      );
    },

    async reopenCycle(
      cycleId: string,
      payload: ReopenCycleRequest,
      options: RequestOptions = {},
    ): Promise<ReopenCycleResponse> {
      return requestJson<ReopenCycleResponse>(
        `${cycleUrl(normalizedBaseUrl, cycleId)}/reopen`,
        jsonRequest("POST", payload, options),
        "Cycle reopen failed",
      );
    },

    async refreshCycleSnapshot(
      cycleId: string,
      payload: RefreshCycleSnapshotRequest,
      options: RequestOptions = {},
    ): Promise<CycleResource> {
      return requestJson<CycleResource>(
        `${cycleUrl(normalizedBaseUrl, cycleId)}/snapshot/refresh`,
        jsonRequest("POST", payload, options),
        "Cycle snapshot refresh failed",
      );
    },

    async listCycleItems(
      cycleId: string,
      query: CycleItemListQuery = {},
      signal?: AbortSignal,
    ): Promise<CycleItemPageResponse> {
      return requestJson<CycleItemPageResponse>(
        `${cycleUrl(normalizedBaseUrl, cycleId)}/items${queryString(query)}`,
        { signal },
        "Cycle item list failed",
      );
    },

    async refreshCycleItemSnapshot(
      cycleItemId: string,
      payload: RefreshCycleItemSnapshotRequest,
      options: RequestOptions = {},
    ): Promise<RefreshCycleItemSnapshotResponse> {
      return requestJson<RefreshCycleItemSnapshotResponse>(
        `${cycleItemUrl(normalizedBaseUrl, cycleItemId)}/snapshot/refresh`,
        jsonRequest("POST", payload, options),
        "Cycle item snapshot refresh failed",
      );
    },

    async startExecutionAttempt(
      cycleItemId: string,
      payload: StartExecutionAttemptRequest,
      options: RequestOptions = {},
    ): Promise<ExecutionAttempt> {
      return requestJson<ExecutionAttempt>(
        `${cycleItemUrl(normalizedBaseUrl, cycleItemId)}/attempts`,
        jsonRequest("POST", payload, options),
        "Execution attempt start failed",
      );
    },

    async recordExecutionAttemptResult(
      attemptId: string,
      payload: CompleteExecutionAttemptRequest,
      options: RequestOptions = {},
    ): Promise<ExecutionAttempt> {
      return requestJson<ExecutionAttempt>(
        `${attemptUrl(normalizedBaseUrl, attemptId)}/result`,
        jsonRequest("POST", payload, options),
        "Execution attempt result failed",
      );
    },

    async retryExecutionAttempt(
      cycleItemId: string,
      payload: RetryExecutionAttemptRequest,
      options: RequestOptions = {},
    ): Promise<RetryExecutionAttemptResponse> {
      return requestJson<RetryExecutionAttemptResponse>(
        `${cycleItemUrl(normalizedBaseUrl, cycleItemId)}/attempts/retry`,
        jsonRequest("POST", payload, options),
        "Execution attempt retry failed",
      );
    },

    async listExecutionAttemptHistory(
      cycleItemId: string,
      query: ExecutionAttemptHistoryQuery = {},
      signal?: AbortSignal,
    ): Promise<ExecutionAttemptHistoryPageResponse> {
      return requestJson<ExecutionAttemptHistoryPageResponse>(
        `${cycleItemUrl(normalizedBaseUrl, cycleItemId)}/attempts${queryString(query)}`,
        { signal },
        "Execution attempt history failed",
      );
    },

    async getStepExecution(
      cycleItemId: string,
      platform: StepExecution["platform"],
      signal?: AbortSignal,
    ): Promise<StepExecution> {
      return requestJson<StepExecution>(
        `${cycleItemUrl(normalizedBaseUrl, cycleItemId)}/executions/${platform}/steps`,
        { signal },
        "Step execution request failed",
      );
    },

    async recordStepExecutionResults(
      cycleItemId: string,
      platform: StepExecution["platform"],
      payload: RecordStepResultsRequest,
      options: RequestOptions = {},
    ): Promise<StepExecution> {
      return requestJson<StepExecution>(
        `${cycleItemUrl(normalizedBaseUrl, cycleItemId)}/executions/${platform}/steps`,
        jsonRequest("PATCH", payload, options),
        "Step execution result failed",
      );
    },

    async getCycleProgress(
      cycleId: string,
      query: CycleProgressQuery = {},
      signal?: AbortSignal,
    ): Promise<CycleProgressResponse> {
      return requestJson<CycleProgressResponse>(
        `${cycleUrl(normalizedBaseUrl, cycleId)}/progress${queryString(query)}`,
        { signal },
        "Cycle progress request failed",
      );
    },

    async getCycleOverallProgress(
      cycleId: string,
      signal?: AbortSignal,
    ): Promise<OverallProgress> {
      return requestJson<OverallProgress>(
        `${cycleUrl(normalizedBaseUrl, cycleId)}/progress/overall`,
        { signal },
        "Cycle overall progress request failed",
      );
    },

    async getCyclePlatformProgress(
      cycleId: string,
      signal?: AbortSignal,
    ): Promise<PlatformProgress[]> {
      return requestJson<PlatformProgress[]>(
        `${cycleUrl(normalizedBaseUrl, cycleId)}/progress/platform`,
        { signal },
        "Cycle platform progress request failed",
      );
    },

    async getCycleOwnerProgress(
      cycleId: string,
      signal?: AbortSignal,
    ): Promise<OwnerProgress[]> {
      return requestJson<OwnerProgress[]>(
        `${cycleUrl(normalizedBaseUrl, cycleId)}/progress/owner`,
        { signal },
        "Cycle owner progress request failed",
      );
    },

    async getCycleWorkItemProgress(
      cycleId: string,
      signal?: AbortSignal,
    ): Promise<WorkItemProgress[]> {
      return requestJson<WorkItemProgress[]>(
        `${cycleUrl(normalizedBaseUrl, cycleId)}/progress/work-item`,
        { signal },
        "Cycle work-item progress request failed",
      );
    },

    async getCycleLabelProgress(
      cycleId: string,
      signal?: AbortSignal,
    ): Promise<LabelProgress[]> {
      return requestJson<LabelProgress[]>(
        `${cycleUrl(normalizedBaseUrl, cycleId)}/progress/label`,
        { signal },
        "Cycle label progress request failed",
      );
    },

    async listIssues(
      query: IssueListQuery = {},
      signal?: AbortSignal,
    ): Promise<IssuePage> {
      return requestJson<IssuePage>(
        `${normalizedBaseUrl}/api/issues${queryString(query)}`,
        { signal },
        "Issue list request failed",
      );
    },

    async createIssue(
      payload: CreateIssueRequest,
      options: RequestOptions = {},
    ): Promise<IssueResource> {
      return requestJson<IssueResource>(
        `${normalizedBaseUrl}/api/issues`,
        jsonRequest("POST", payload, withBodyIdempotency(payload, options)),
        "Issue create failed",
      );
    },

    async createIssueFromExecutionAttempt(
      attemptId: string,
      payload: CreateIssueFromAttemptRequest,
      options: RequestOptions = {},
    ): Promise<IssueResource> {
      return requestJson<IssueResource>(
        `${attemptUrl(normalizedBaseUrl, attemptId)}/issues`,
        jsonRequest("POST", payload, withBodyIdempotency(payload, options)),
        "Issue create from attempt failed",
      );
    },

    async getIssue(
      issueId: string,
      signal?: AbortSignal,
      includeDeleted = false,
    ): Promise<IssueResource> {
      return requestJson<IssueResource>(
        `${issueUrl(normalizedBaseUrl, issueId)}${queryString(includeDeleted ? { includeDeleted } : {})}`,
        { signal },
        "Issue request failed",
      );
    },

    async updateIssue(
      issueId: string,
      payload: UpdateIssueRequest,
      options: RequestOptions = {},
    ): Promise<IssueResource> {
      return requestJson<IssueResource>(
        issueUrl(normalizedBaseUrl, issueId),
        jsonRequest("PATCH", payload, options),
        "Issue update failed",
      );
    },

    async deleteIssue(
      issueId: string,
      payload: DeleteIssueRequest,
      options: RequestOptions = {},
    ): Promise<DeleteIssueResponse> {
      return requestJson<DeleteIssueResponse>(
        issueUrl(normalizedBaseUrl, issueId),
        jsonRequest("DELETE", payload, options),
        "Issue delete failed",
      );
    },

    async restoreIssue(
      issueId: string,
      payload: RestoreIssueRequest,
      options: RequestOptions = {},
    ): Promise<RestoreIssueResponse> {
      return requestJson<RestoreIssueResponse>(
        `${issueUrl(normalizedBaseUrl, issueId)}/restore`,
        jsonRequest("POST", payload, options),
        "Issue restore failed",
      );
    },

    async permanentlyDeleteIssue(
      issueId: string,
      payload: PermanentDeleteIssueRequest,
      options: RequestOptions = {},
    ): Promise<PermanentDeleteIssueResponse> {
      return requestJson<PermanentDeleteIssueResponse>(
        `${issueUrl(normalizedBaseUrl, issueId)}/permanent`,
        jsonRequest("DELETE", payload, options),
        "Issue permanent delete failed",
      );
    },

    async listIssueLinks(
      issueId: string,
      query: PageQuery = {},
      signal?: AbortSignal,
    ): Promise<IssueLinkPage> {
      return requestJson<IssueLinkPage>(
        `${issueUrl(normalizedBaseUrl, issueId)}/links${queryString(query)}`,
        { signal },
        "Issue link list failed",
      );
    },

    async createIssueLink(
      issueId: string,
      payload: CreateIssueLinkRequest,
      options: RequestOptions = {},
    ): Promise<IssueLinkResource> {
      return requestJson<IssueLinkResource>(
        `${issueUrl(normalizedBaseUrl, issueId)}/links`,
        jsonRequest("POST", payload, withBodyIdempotency(payload, options)),
        "Issue link create failed",
      );
    },

    async deleteIssueLink(
      issueId: string,
      issueLinkId: string,
      payload: DeleteIssueLinkRequest,
      options: RequestOptions = {},
    ): Promise<DeleteIssueLinkResponse> {
      return requestJson<DeleteIssueLinkResponse>(
        `${issueUrl(normalizedBaseUrl, issueId)}/links/${encodeURIComponent(issueLinkId)}`,
        jsonRequest("DELETE", payload, options),
        "Issue link delete failed",
      );
    },

    async listIssueComments(
      issueId: string,
      query: PageQuery = {},
      signal?: AbortSignal,
    ): Promise<IssueCommentPage> {
      return requestJson<IssueCommentPage>(
        `${issueUrl(normalizedBaseUrl, issueId)}/comments${queryString(query)}`,
        { signal },
        "Issue comment list failed",
      );
    },

    async createIssueComment(
      issueId: string,
      payload: CreateIssueCommentRequest,
      options: RequestOptions = {},
    ): Promise<IssueCommentResource> {
      return requestJson<IssueCommentResource>(
        `${issueUrl(normalizedBaseUrl, issueId)}/comments`,
        jsonRequest("POST", payload, withBodyIdempotency(payload, options)),
        "Issue comment create failed",
      );
    },

    async listIssueHistory(
      issueId: string,
      query: PageQuery = {},
      signal?: AbortSignal,
    ): Promise<IssueHistoryPage> {
      return requestJson<IssueHistoryPage>(
        `${issueUrl(normalizedBaseUrl, issueId)}/history${queryString(query)}`,
        { signal },
        "Issue history request failed",
      );
    },

    async uploadIssueAttachment(
      issueId: string,
      metadata: AttachmentUploadMetadata,
      file: Blob,
      options: RequestOptions = {},
    ): Promise<AttachmentUploadResponse> {
      return uploadAttachmentDirect(
        normalizedBaseUrl,
        `${issueUrl(normalizedBaseUrl, issueId)}/attachment-upload-sessions`,
        metadata,
        file,
        options,
      );
    },

    async listAttachments(
      query: AttachmentListQuery = {},
      signal?: AbortSignal,
    ): Promise<AttachmentPage> {
      return requestJson<AttachmentPage>(
        `${normalizedBaseUrl}/api/attachments${queryString(query)}`,
        { signal },
        "Attachment list failed",
      );
    },

    async uploadExecutionAttemptAttachment(
      attemptId: string,
      metadata: AttachmentUploadMetadata,
      file: Blob,
      options: RequestOptions = {},
    ): Promise<AttachmentUploadResponse> {
      return uploadAttachmentDirect(
        normalizedBaseUrl,
        `${attemptUrl(normalizedBaseUrl, attemptId)}/attachment-upload-sessions`,
        metadata,
        file,
        options,
      );
    },

    async downloadAttachmentContent(
      attachmentId: string,
      signal?: AbortSignal,
    ): Promise<Blob> {
      return requestBlob(
        `${attachmentUrl(normalizedBaseUrl, attachmentId)}/content`,
        { signal },
        "Attachment download failed",
      );
    },

    async getObjectStorageStatus(signal?: AbortSignal): Promise<ObjectStorageStatus> {
      return requestJson<ObjectStorageStatus>(
        `${normalizedBaseUrl}/api/integrations/object-storage/status`,
        { signal },
        "Object storage status request failed",
      );
    },

    async getObjectStorageObservations(
      signal?: AbortSignal,
    ): Promise<StorageUsageObservation> {
      return requestJson<StorageUsageObservation>(
        `${normalizedBaseUrl}/api/integrations/object-storage/observations`,
        { signal },
        "Object storage observation request failed",
      );
    },

    async deleteAttachment(
      attachmentId: string,
      payload: SoftDeleteAttachmentRequest,
      options: RequestOptions = {},
    ): Promise<SoftDeleteAttachmentResponse> {
      return requestJson<SoftDeleteAttachmentResponse>(
        attachmentUrl(normalizedBaseUrl, attachmentId),
        jsonRequest("DELETE", payload, options),
        "Attachment delete failed",
      );
    },

    async reconcileObjectStorageOrphans(
      payload: OrphanReconcileRequest,
      options: RequestOptions = {},
    ): Promise<OrphanReconcileResponse> {
      return requestJson<OrphanReconcileResponse>(
        `${normalizedBaseUrl}/api/integrations/object-storage/reconcile-orphans`,
        jsonRequest("POST", payload, withBodyIdempotency(payload, options)),
        "Object storage orphan reconciliation failed",
      );
    },

    async verifyObjectStorageMetadata(
      payload: MetadataIntegrityRequest,
      options: RequestOptions = {},
    ): Promise<MetadataIntegrityResponse> {
      return requestJson<MetadataIntegrityResponse>(
        `${normalizedBaseUrl}/api/integrations/object-storage/verify-metadata`,
        jsonRequest("POST", payload, options),
        "Object storage metadata verification failed",
      );
    },

    async verifyObjectStorageRestoreSample(
      payload: RestoreSampleRequest,
      options: RequestOptions = {},
    ): Promise<RestoreSampleResponse> {
      return requestJson<RestoreSampleResponse>(
        `${normalizedBaseUrl}/api/integrations/object-storage/restore-sample`,
        jsonRequest("POST", payload, options),
        "Object storage restore sample failed",
      );
    },

    async getLegacyAttachmentInventory(
      query: LegacyAttachmentInventoryQuery = {},
      signal?: AbortSignal,
    ): Promise<LegacyAttachmentInventory> {
      return requestJson<LegacyAttachmentInventory>(
        `${normalizedBaseUrl}/api/integrations/object-storage/legacy-attachments${queryString(query)}`,
        { signal },
        "Legacy attachment inventory request failed",
      );
    },

    async getOverviewKpis(
      query: OverviewKpiQuery = {},
      signal?: AbortSignal,
    ): Promise<OverviewKpi> {
      return requestJson<OverviewKpi>(
        `${normalizedBaseUrl}/api/dashboard/overview${queryString(query)}`,
        { signal },
        "Overview KPI request failed",
      );
    },

    async listCycleReports(
      query: CycleReportQuery = {},
      signal?: AbortSignal,
    ): Promise<CycleReportPage> {
      return requestJson<CycleReportPage>(
        `${normalizedBaseUrl}/api/reports/cycles${queryString(query)}`,
        { signal },
        "Cycle report request failed",
      );
    },

    async getFolderTree(signal?: AbortSignal): Promise<FolderTreeResponse> {
      return requestJson<FolderTreeResponse>(
        `${normalizedBaseUrl}/api/folders/tree`,
        { signal },
        "Folder tree request failed",
      );
    },

    async getFolderChildren(
      parentId?: string | null,
      signal?: AbortSignal,
    ): Promise<FolderChildrenResponse> {
      const search = parentId === undefined ? "" : queryString({ parentId });
      return requestJson<FolderChildrenResponse>(
        `${normalizedBaseUrl}/api/folders/children${search}`,
        { signal },
        "Folder children request failed",
      );
    },

    async createFolder(
      payload: CreateFolderRequest,
      options: RequestOptions = {},
    ): Promise<FolderMutationResponse> {
      return requestJson<FolderMutationResponse>(
        `${normalizedBaseUrl}/api/folders`,
        jsonRequest("POST", payload, options),
        "Folder create failed",
      );
    },

    async renameFolder(
      folderId: string,
      payload: RenameFolderRequest,
      options: RequestOptions = {},
    ): Promise<FolderMutationResponse> {
      return requestJson<FolderMutationResponse>(
        folderUrl(normalizedBaseUrl, folderId),
        jsonRequest("PATCH", payload, options),
        "Folder rename failed",
      );
    },

    async moveFolder(
      folderId: string,
      payload: MoveFolderRequest,
      options: RequestOptions = {},
    ): Promise<FolderMutationResponse> {
      return requestJson<FolderMutationResponse>(
        `${folderUrl(normalizedBaseUrl, folderId)}/move`,
        jsonRequest("POST", payload, options),
        "Folder move failed",
      );
    },

    async sortFolder(
      folderId: string,
      payload: SortFolderRequest,
      options: RequestOptions = {},
    ): Promise<FolderMutationResponse> {
      return requestJson<FolderMutationResponse>(
        `${folderUrl(normalizedBaseUrl, folderId)}/sort`,
        jsonRequest("POST", payload, options),
        "Folder sort failed",
      );
    },

    async previewFolderDelete(
      folderId: string,
      signal?: AbortSignal,
    ): Promise<FolderDeletePreviewResponse> {
      return requestJson<FolderDeletePreviewResponse>(
        `${folderUrl(normalizedBaseUrl, folderId)}/delete-preview`,
        { signal },
        "Folder delete preview failed",
      );
    },

    async softDeleteFolder(
      folderId: string,
      payload: SoftDeleteFolderRequest,
      confirm = false,
      options: RequestOptions = {},
    ): Promise<FolderMutationResponse> {
      return requestJson<FolderMutationResponse>(
        `${folderUrl(normalizedBaseUrl, folderId)}${queryString({ confirm })}`,
        jsonRequest("DELETE", payload, options),
        "Folder delete failed",
      );
    },

    async restoreFolder(
      folderId: string,
      payload: RestoreFolderRequest,
      options: RequestOptions = {},
    ): Promise<FolderMutationResponse> {
      return requestJson<FolderMutationResponse>(
        `${folderUrl(normalizedBaseUrl, folderId)}/restore`,
        jsonRequest("POST", payload, options),
        "Folder restore failed",
      );
    },

    async listKnowledgeNodes(
      query: KnowledgeNodeListQuery = {},
      signal?: AbortSignal,
    ): Promise<KnowledgeNodePage> {
      return requestJson<KnowledgeNodePage>(
        `${normalizedBaseUrl}/api/knowledge/nodes${queryString(query)}`,
        { signal },
        "Knowledge node list failed",
      );
    },

    async getKnowledgeNode(
      nodeId: string,
      signal?: AbortSignal,
    ): Promise<KnowledgeNodeDetail> {
      return requestJson<KnowledgeNodeDetail>(
        knowledgeNodeUrl(normalizedBaseUrl, nodeId),
        { signal },
        "Knowledge node request failed",
      );
    },

    async listKnowledgeNodeEvidence(
      nodeId: string,
      signal?: AbortSignal,
    ): Promise<KnowledgeEvidencePage> {
      return requestJson<KnowledgeEvidencePage>(
        `${knowledgeNodeUrl(normalizedBaseUrl, nodeId)}/evidence`,
        { signal },
        "Knowledge evidence request failed",
      );
    },

    async patchKnowledgeStatus(
      payload: KnowledgeStatusPatchRequest,
      options: RequestOptions = {},
    ): Promise<KnowledgeStatusPatchResponse> {
      return requestJson<KnowledgeStatusPatchResponse>(
        `${normalizedBaseUrl}/api/knowledge/status`,
        jsonRequest("PATCH", payload, options),
        "Knowledge status patch failed",
      );
    },

    async listKnowledgeNodeAudit(
      nodeId: string,
      signal?: AbortSignal,
    ): Promise<KnowledgeStatusAuditPage> {
      return requestJson<KnowledgeStatusAuditPage>(
        `${knowledgeNodeUrl(normalizedBaseUrl, nodeId)}/audit`,
        { signal },
        "Knowledge audit request failed",
      );
    },

    async searchKnowledge(
      payload: HybridSearchRequest,
      options: RequestOptions = {},
    ): Promise<HybridSearchResponse> {
      return requestJson<HybridSearchResponse>(
        `${normalizedBaseUrl}/api/knowledge/search`,
        jsonRequest("POST", payload, options),
        "Knowledge search failed",
      );
    },

    async exploreKnowledgeOntology(
      payload: OntologyExplorerRequest,
      options: RequestOptions = {},
    ): Promise<OntologyExplorerResponse> {
      return requestJson<OntologyExplorerResponse>(
        `${normalizedBaseUrl}/api/knowledge/explorer`,
        jsonRequest("POST", payload, options),
        "Knowledge ontology exploration failed",
      );
    },

    async createKnowledgeProjectionJob(
      payload: KnowledgeProjectionJobRequest,
      options: RequestOptions = {},
    ): Promise<KnowledgeJobResponse> {
      return requestJson<KnowledgeJobResponse>(
        `${normalizedBaseUrl}/api/knowledge/jobs/projection`,
        jsonRequest("POST", payload, withBodyIdempotency(payload, options)),
        "Knowledge projection job request failed",
      );
    },

    async createKnowledgeReindexJob(
      payload: KnowledgeReindexJobRequest,
      options: RequestOptions = {},
    ): Promise<KnowledgeJobResponse> {
      return requestJson<KnowledgeJobResponse>(
        `${normalizedBaseUrl}/api/knowledge/jobs/reindex`,
        jsonRequest("POST", payload, withBodyIdempotency(payload, options)),
        "Knowledge reindex job request failed",
      );
    },
  };
}

async function requestJson<Response>(
  url: string,
  init: RequestInit,
  failureMessage: string,
): Promise<Response> {
  const response = await fetch(url, init);
  const hasJson = response.headers.get("content-type")?.includes("application/json") ?? false;
  const body = hasJson ? ((await response.json()) as unknown) : undefined;
  if (!response.ok) {
    throw new ApiClientError(failureMessage, response.status, body as ApiErrorResponse | undefined);
  }
  return body as Response;
}

async function requestBlob(
  url: string,
  init: RequestInit,
  failureMessage: string,
): Promise<Blob> {
  const response = await fetch(url, init);
  if (!response.ok) {
    throw new ApiClientError(
      failureMessage,
      response.status,
      await errorResponse(response),
    );
  }
  return response.blob();
}

async function errorResponse(response: Response): Promise<ApiErrorResponse | undefined> {
  if (!response.headers.get("content-type")?.includes("application/json")) return undefined;
  return (await response.json()) as ApiErrorResponse;
}

async function uploadAttachmentDirect(
  baseUrl: string,
  sessionUrl: string,
  metadata: AttachmentUploadMetadata,
  file: Blob,
  options: RequestOptions,
): Promise<AttachmentUploadResponse> {
  const sha256 = await sha256Hex(file);
  if (metadata.clientSha256 && metadata.clientSha256 !== sha256) {
    throw new Error("Attachment clientSha256 does not match the selected file");
  }
  const sessionPayload: AttachmentUploadSessionRequest = {
    idempotencyKey: metadata.idempotencyKey,
    filename: metadata.filename,
    declaredMimeType: metadata.declaredMimeType,
    declaredSizeBytes: metadata.declaredSizeBytes,
    clientSha256: sha256,
  };
  const session = await requestJson<AttachmentUploadSessionResponse>(
    sessionUrl,
    jsonRequest("POST", sessionPayload, {
      ...options,
      idempotencyKey: metadata.idempotencyKey,
    }),
    "Attachment upload session failed",
  );
  const uploadResponse = await fetch(session.upload.url, {
    method: session.upload.method,
    headers: session.upload.headers,
    body: file,
    signal: options.signal,
  });
  if (!uploadResponse.ok) {
    throw new ApiClientError(
      "Attachment object upload failed",
      uploadResponse.status,
    );
  }
  // Drain the presigned PUT response before the caller can navigate away. Otherwise Chrome may
  // report the successful object write as net::ERR_ABORTED after resolving fetch on headers.
  await uploadResponse.arrayBuffer();
  const completePayload: CompleteAttachmentUploadRequest = {
    idempotencyKey: metadata.idempotencyKey,
    uploadToken: session.uploadToken,
  };
  return requestJson<AttachmentUploadResponse>(
    `${baseUrl}/api/attachment-upload-sessions/complete`,
    jsonRequest("POST", completePayload, {
      ...options,
      idempotencyKey: metadata.idempotencyKey,
    }),
    "Attachment upload completion failed",
  );
}

async function sha256Hex(file: Blob): Promise<string> {
  const bytes = await file.arrayBuffer();
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function withBodyIdempotency<Payload extends { idempotencyKey: string }>(
  payload: Payload,
  options: RequestOptions,
): RequestOptions {
  if (options.idempotencyKey && options.idempotencyKey !== payload.idempotencyKey) {
    throw new Error("Request idempotencyKey must match options.idempotencyKey");
  }
  return { ...options, idempotencyKey: payload.idempotencyKey };
}

function jsonRequest(
  method: "DELETE" | "PATCH" | "POST" | "PUT",
  payload: object,
  options: RequestOptions,
): RequestInit {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;
  if (options.traceId) headers["X-Trace-Id"] = options.traceId;
  if (options.actorLabel) headers["X-Actor-Label"] = options.actorLabel;
  return {
    method,
    headers,
    body: JSON.stringify(payload),
    signal: options.signal,
  };
}

function qualityReviewJsonRequest(
  payload: object,
  options: RequestOptions,
  reviewerToken: string,
): RequestInit {
  const request = jsonRequest("POST", payload, {
    signal: options.signal,
    idempotencyKey: options.idempotencyKey,
    traceId: options.traceId,
  });
  const headers = request.headers as Record<string, string>;
  headers["X-Quality-Reviewer-Token"] = reviewerToken;
  return request;
}

function folderUrl(baseUrl: string, folderId: string): string {
  return `${baseUrl}/api/folders/${encodeURIComponent(folderId)}`;
}

function caseUrl(baseUrl: string, caseId: string): string {
  return `${baseUrl}/api/test-cases/${encodeURIComponent(caseId)}`;
}

function cycleUrl(baseUrl: string, cycleId: string): string {
  return `${baseUrl}/api/cycles/${encodeURIComponent(cycleId)}`;
}

function cycleItemUrl(baseUrl: string, cycleItemId: string): string {
  return `${baseUrl}/api/cycle-items/${encodeURIComponent(cycleItemId)}`;
}

function attemptUrl(baseUrl: string, attemptId: string): string {
  return `${baseUrl}/api/execution-attempts/${encodeURIComponent(attemptId)}`;
}

function issueUrl(baseUrl: string, issueId: string): string {
  return `${baseUrl}/api/issues/${encodeURIComponent(issueId)}`;
}

function attachmentUrl(baseUrl: string, attachmentId: string): string {
  return `${baseUrl}/api/attachments/${encodeURIComponent(attachmentId)}`;
}

function knowledgeNodeUrl(baseUrl: string, nodeId: string): string {
  return `${baseUrl}/api/knowledge/nodes/${encodeURIComponent(nodeId)}`;
}

function generationQualityCandidateUrl(baseUrl: string, candidateId: string): string {
  return `${baseUrl}/api/generation-quality/review-candidates/${encodeURIComponent(candidateId)}`;
}

function queryString(values: Record<string, unknown>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (Array.isArray(value)) {
      for (const item of value) search.append(key, String(item));
    } else if (value !== undefined && value !== null) {
      search.set(key, String(value));
    }
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}
