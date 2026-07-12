import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Toaster } from "sonner";
import ErrorBoundary from "./components/ErrorBoundary";
import RequireRole from "./components/RequireRole";
import Sidebar from "./components/Sidebar";
import AgentDetailPage from "./pages/AgentDetailPage";
import CatalogPage from "./pages/CatalogPage";
import CatalogDetailPage from "./pages/CatalogDetailPage";
import AgentListPage from "./pages/AgentListPage";
import AdminAccessPage from "./pages/AdminAccessPage";
import AdminApprovalAuthorityPage from "./pages/AdminApprovalAuthorityPage";
import AdminGrantsPage from "./pages/AdminGrantsPage";
import AdminArtifactsPage from "./pages/AdminArtifactsPage";
import AdminPublishRequestsPage from "./pages/AdminPublishRequestsPage";
import AgentChatPage from "./pages/AgentChatPage";
import MyAgentsPage from "./pages/MyAgentsPage";
import DeploymentsPage from "./pages/DeploymentsPage";
import AgentGraphsPage from "./pages/AgentGraphsPage";
import CanvasPage from "./pages/CanvasPage";
import CreateAgentPage from "./pages/CreateAgentPage";
import DatasetsPage from "./pages/DatasetsPage";
import DeploymentOverviewPage from "./pages/DeploymentOverviewPage";
import EvalResultsPage from "./pages/EvalResultsPage";
import HITLDashboardPage from "./pages/HITLDashboardPage";
import PlaygroundPage from "./pages/PlaygroundPage";
import ProvidersPage from "./pages/ProvidersPage";
import SkillsPage from "./pages/SkillsPage";
import ToolsPage from "./pages/ToolsPage";
import ApprovalsInboxPage from "./pages/ApprovalsInboxPage";
import WorkflowDeploymentOverviewPage from "./pages/WorkflowDeploymentOverviewPage";
import WorkflowDetailPage from "./pages/WorkflowDetailPage";
import WorkflowsPage from "./pages/WorkflowsPage";
import CatalogChatPage from "./pages/CatalogChatPage";
import WorkflowBuilderPage from "./pages/WorkflowBuilderPage";
import ObservabilityTracesPage from "./pages/ObservabilityTracesPage";
import ObservabilityDashboardPage from "./pages/ObservabilityDashboardPage";
import ObservabilityComparePage from "./pages/ObservabilityComparePage";
import CostConsolePage from "./pages/CostConsolePage";
import CredentialsPage from "./pages/CredentialsPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 10_000 },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <div className="flex min-h-screen">
          <Sidebar />
          <main className="flex-1 overflow-auto">
            <ErrorBoundary>
            <Routes>
              <Route path="/" element={<AgentListPage />} />
              <Route path="/agents" element={<AgentListPage />} />
              <Route path="/agents/new" element={<CreateAgentPage />} />
              <Route path="/agents/:name/chat" element={<AgentChatPage />} />
              {/* /agents/:name/deploy removed — deploy is now a modal on AgentDetailPage */}
              <Route path="/agents/:name/d/:depId/chat" element={<AgentChatPage />} />
              <Route path="/agents/:name/d/:depId" element={<DeploymentOverviewPage />} />
              <Route path="/agents/:name" element={<AgentDetailPage />} />
              <Route path="/providers" element={<ProvidersPage />} />
              <Route path="/credentials" element={<CredentialsPage />} />
              <Route path="/agent-graphs" element={<AgentGraphsPage />} />
              <Route path="/agent-graphs/new" element={<CanvasPage />} />
              <Route path="/agent-graphs/:id" element={<CanvasPage />} />
              <Route path="/workflows" element={<WorkflowsPage />} />
              <Route path="/workflows/new" element={<WorkflowBuilderPage />} />
              <Route path="/workflows/:id/d/:depId" element={<WorkflowDeploymentOverviewPage />} />
              <Route path="/workflows/:id/builder" element={<WorkflowBuilderPage />} />
              <Route path="/workflows/:id" element={<WorkflowDetailPage />} />
              <Route path="/tools" element={<ToolsPage />} />
              <Route path="/skills" element={<SkillsPage />} />
              <Route path="/my-agents" element={<MyAgentsPage />} />
              <Route path="/catalog" element={<CatalogPage />} />
              <Route path="/catalog/:artifactId" element={<CatalogDetailPage />} />
              <Route path="/catalog/:artifactId/chat" element={<CatalogChatPage />} />
              <Route path="/admin/artifacts" element={<RequireRole minRole="platform-admin"><AdminArtifactsPage /></RequireRole>} />
              <Route path="/admin/publish-requests" element={<RequireRole minRole="platform-admin"><AdminPublishRequestsPage /></RequireRole>} />
              <Route path="/admin/access" element={<RequireRole minRole="platform-admin"><AdminAccessPage /></RequireRole>} />
              <Route path="/admin/grants" element={<RequireRole minRole="platform-admin"><AdminGrantsPage /></RequireRole>} />
              <Route path="/admin/approval-authority" element={<RequireRole minRole="platform-admin"><AdminApprovalAuthorityPage /></RequireRole>} />
              <Route path="/deployments" element={<DeploymentsPage />} />
              <Route path="/approvals" element={<ApprovalsInboxPage />} />
              <Route path="/hitl" element={<HITLDashboardPage />} />
              <Route path="/playground" element={<PlaygroundPage />} />
              <Route path="/playground/datasets" element={<DatasetsPage />} />
              <Route path="/playground/eval-runs/:evalRunId" element={<EvalResultsPage />} />
              <Route path="/observability/traces" element={<ObservabilityTracesPage />} />
              <Route
                path="/observability/dashboard/production"
                element={<ObservabilityDashboardPage environment="production" />}
              />
              <Route
                path="/observability/dashboard/sandbox"
                element={<ObservabilityDashboardPage environment="sandbox" />}
              />
              {/* Legacy path → production (the admin's default view). */}
              <Route
                path="/observability/dashboard"
                element={<Navigate to="/observability/dashboard/production" replace />}
              />
              <Route path="/observability/compare" element={<ObservabilityComparePage />} />
              <Route path="/observability/costs" element={<CostConsolePage />} />
            </Routes>
            </ErrorBoundary>
          </main>
        </div>
        {/* The toast sink lives OUTSIDE the routes boundary; a non-string toast
            content would otherwise crash it and blank the whole app. Contain it. */}
        <ErrorBoundary fallback={null}>
          <Toaster position="top-right" richColors closeButton />
        </ErrorBoundary>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
