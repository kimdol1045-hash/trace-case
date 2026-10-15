import { AppShell } from "@tc/ui";
import { Navigate, Outlet, Route, Routes } from "react-router-dom";

import { CycleCreatePage, CycleDetailPage, CycleExecutionPage, CycleWorkspacePage } from "./cycle-workspace-page";
import { cycleMockFallbackEnabled } from "./cycle-service";
import { FolderLibraryPage } from "./folder-library-page";
import { GenerationComparisonPage } from "./generation-comparison-page";
import { GenerationFailurePage } from "./generation-failure-page";
import { GenerationJobPage } from "./generation-job-page";
import { GenerationQualityReviewPage } from "./generation-quality-review-page";
import { IssueCreatePage, IssueDetailPage, IssueWorkspacePage } from "./issue-workspace-page";
import { KnowledgeWorkspacePage } from "./knowledge-workspace-page";
import { AppBrand, AppNavigation } from "./navigation";
import { NotFoundPage } from "./pages";
import { SlackWorkItemDetailPage, SlackWorkItemsPage } from "./slack-workspace-page";
import { IntegrationsPage, OverviewDashboardPage, ReportsPage } from "./sprint4-dashboard-pages";
import { TestCaseCreatePage } from "./test-case-create-page";
import { TestCaseDetailPage } from "./test-case-detail-page";
import { TestManagementProvider } from "./test-management-demo";
import { WorkItemDetailPage } from "./work-item-detail-page";

/** Test-only eager route tree; production uses the lazy tree in app.tsx. */
export function AppRoutes() {
  return (
    <AppShell brand={<AppBrand />} navigation={<AppNavigation />}>
      <Routes>
        <Route path="/" element={<OverviewDashboardPage />} />
        <Route element={<TestManagementRouteBoundary />}>
          <Route path="/test-cases" element={<FolderLibraryPage />} />
          <Route path="/test-cases/new" element={<TestCaseCreatePage />} />
          <Route path="/test-cases/:caseKey" element={<TestCaseDetailPage />} />
          <Route path="/work-items/:workItemKey" element={<WorkItemDetailPage />} />
          <Route path="/cycles" element={<CycleWorkspacePage />} />
          <Route path="/cycles/new" element={<CycleCreatePage />} />
          <Route path="/cycles/:cycleId/execute" element={<CycleExecutionPage />} />
          <Route path="/cycles/:cycleId" element={<CycleDetailPage />} />
        </Route>
        <Route path="/issues" element={<IssueWorkspacePage />} />
        <Route path="/issues/new" element={<IssueCreatePage />} />
        <Route path="/issues/:issueKey" element={<IssueDetailPage />} />
        <Route path="/reports" element={<ReportsPage />} />
        <Route path="/slack-work-items" element={<SlackWorkItemsPage />} />
        <Route path="/slack-work-items/:workItemId" element={<SlackWorkItemDetailPage />} />
        <Route path="/slack-work-items/:workItemId/generation-jobs" element={<GenerationJobPage />} />
        <Route path="/slack-work-items/:workItemId/generation-jobs/:jobId/comparison" element={<GenerationComparisonPage />} />
        <Route path="/generation/failures" element={<GenerationFailurePage />} />
        <Route path="/generation/quality-review" element={<GenerationQualityReviewPage />} />
        <Route path="/knowledge" element={<KnowledgeWorkspacePage />} />
        <Route path="/integrations" element={<IntegrationsPage />} />
        <Route path="/home" element={<Navigate to="/" replace />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </AppShell>
  );
}

function TestManagementRouteBoundary() {
  return <TestManagementProvider enableDevelopmentFixtures={cycleMockFallbackEnabled}><Outlet /></TestManagementProvider>;
}
