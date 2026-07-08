import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Toaster } from "sonner";
import ErrorBoundary from "./components/ErrorBoundary";
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
import DeployAgentPage from "./pages/DeployAgentPage";
import EvalResultsPage from "./pages/EvalResultsPage";
import HITLDashboardPage from "./pages/HITLDashboardPage";
import PlaygroundPage from "./pages/PlaygroundPage";
import ProvidersPage from "./pages/ProvidersPage";
import SkillsPage from "./pages/SkillsPage";
import ToolsPage from "./pages/ToolsPage";
import ApprovalsInboxPage from "./pages/ApprovalsInboxPage";
import WorkflowsPage from "./pages/WorkflowsPage";
import CatalogChatPage from "./pages/CatalogChatPage";
import WorkflowBuilderPage from "./pages/WorkflowBuilderPage";

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
              <Route path="/agents/new" element={<CreateAgentPage />} />
              <Route path="/agents/:name/chat" element={<AgentChatPage />} />
              <Route path="/agents/:name/deploy" element={<DeployAgentPage />} />
              <Route path="/agents/:name" element={<AgentDetailPage />} />
              <Route path="/providers" element={<ProvidersPage />} />
              <Route path="/agent-graphs" element={<AgentGraphsPage />} />
              <Route path="/agent-graphs/new" element={<CanvasPage />} />
              <Route path="/agent-graphs/:id" element={<CanvasPage />} />
              <Route path="/workflows" element={<WorkflowsPage />} />
              <Route path="/workflows/new" element={<WorkflowBuilderPage />} />
              <Route path="/workflows/:id/builder" element={<WorkflowBuilderPage />} />
              <Route path="/tools" element={<ToolsPage />} />
              <Route path="/skills" element={<SkillsPage />} />
              <Route path="/my-agents" element={<MyAgentsPage />} />
              <Route path="/catalog" element={<CatalogPage />} />
              <Route path="/catalog/:artifactId" element={<CatalogDetailPage />} />
              <Route path="/catalog/:artifactId/chat" element={<CatalogChatPage />} />
              <Route path="/admin/artifacts" element={<AdminArtifactsPage />} />
              <Route path="/admin/publish-requests" element={<AdminPublishRequestsPage />} />
              <Route path="/admin/access" element={<AdminAccessPage />} />
              <Route path="/admin/grants" element={<AdminGrantsPage />} />
              <Route path="/admin/approval-authority" element={<AdminApprovalAuthorityPage />} />
              <Route path="/deployments" element={<DeploymentsPage />} />
              <Route path="/approvals" element={<ApprovalsInboxPage />} />
              <Route path="/hitl" element={<HITLDashboardPage />} />
              <Route path="/playground" element={<PlaygroundPage />} />
              <Route path="/playground/datasets" element={<DatasetsPage />} />
              <Route path="/playground/eval-runs/:evalRunId" element={<EvalResultsPage />} />
            </Routes>
            </ErrorBoundary>
          </main>
        </div>
        <Toaster position="top-right" richColors closeButton />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
