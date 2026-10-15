import { AppShell } from "@tc/ui";
import { lazy, Suspense, type ComponentType, type LazyExoticComponent } from "react";
import { createBrowserRouter, Navigate, Outlet, Route, RouterProvider, Routes } from "react-router-dom";

import { cycleMockFallbackEnabled } from "./cycle-service";
import { AppBrand, AppNavigation } from "./navigation";
import { TestManagementProvider } from "./test-management-demo";

function lazyNamed<T extends object>(
  loader: () => Promise<T>,
  name: keyof T,
): LazyExoticComponent<ComponentType> {
  return lazy(async () => ({ default: (await loader())[name] as ComponentType }));
}

const dashboardPages = () => import("./sprint4-dashboard-pages");
const cyclePages = () => import("./cycle-workspace-page");
const issuePages = () => import("./issue-workspace-page");
const slackPages = () => import("./slack-workspace-page");

const OverviewDashboardPage = lazyNamed(dashboardPages, "OverviewDashboardPage");
const ReportsPage = lazyNamed(dashboardPages, "ReportsPage");
const IntegrationsPage = lazyNamed(dashboardPages, "IntegrationsPage");
const FolderLibraryPage = lazyNamed(() => import("./folder-library-page"), "FolderLibraryPage");
const TestCaseCreatePage = lazyNamed(() => import("./test-case-create-page"), "TestCaseCreatePage");
const TestCaseDetailPage = lazyNamed(() => import("./test-case-detail-page"), "TestCaseDetailPage");
const WorkItemDetailPage = lazyNamed(() => import("./work-item-detail-page"), "WorkItemDetailPage");
const CycleWorkspacePage = lazyNamed(cyclePages, "CycleWorkspacePage");
const CycleCreatePage = lazyNamed(cyclePages, "CycleCreatePage");
const CycleDetailPage = lazyNamed(cyclePages, "CycleDetailPage");
const CycleExecutionPage = lazyNamed(cyclePages, "CycleExecutionPage");
const IssueWorkspacePage = lazyNamed(issuePages, "IssueWorkspacePage");
const IssueCreatePage = lazyNamed(issuePages, "IssueCreatePage");
const IssueDetailPage = lazyNamed(issuePages, "IssueDetailPage");
const SlackWorkItemsPage = lazyNamed(slackPages, "SlackWorkItemsPage");
const SlackWorkItemDetailPage = lazyNamed(slackPages, "SlackWorkItemDetailPage");
const GenerationComparisonPage = lazyNamed(() => import("./generation-comparison-page"), "GenerationComparisonPage");
const GenerationJobPage = lazyNamed(() => import("./generation-job-page"), "GenerationJobPage");
const GenerationFailurePage = lazyNamed(() => import("./generation-failure-page"), "GenerationFailurePage");
const GenerationQualityReviewPage = lazyNamed(() => import("./generation-quality-review-page"), "GenerationQualityReviewPage");
const KnowledgeWorkspacePage = lazyNamed(() => import("./knowledge-workspace-page"), "KnowledgeWorkspacePage");
const NotFoundPage = lazyNamed(() => import("./pages"), "NotFoundPage");

function RouteLoadingPage() {
  return (
    <section className="tm-route-loading" role="status" aria-live="polite" aria-label="화면 불러오는 중">
      <span aria-hidden="true" />
      <strong>화면을 불러오는 중입니다.</strong>
    </section>
  );
}

function LazyAppRoutes() {
  return (
    <AppShell brand={<AppBrand />} navigation={<AppNavigation />}>
      <Suspense fallback={<RouteLoadingPage />}>
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
      </Suspense>
    </AppShell>
  );
}

function TestManagementRouteBoundary() {
  return <TestManagementProvider enableDevelopmentFixtures={cycleMockFallbackEnabled}><Outlet /></TestManagementProvider>;
}

export function App() {
  appRouter ??= createBrowserRouter([{ path: "*", element: <LazyAppRoutes /> }]);
  return <RouterProvider router={appRouter} />;
}

let appRouter: ReturnType<typeof createBrowserRouter> | undefined;
