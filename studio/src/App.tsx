import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Toaster } from "sonner";
import Sidebar from "./components/Sidebar";
import AgentDetailPage from "./pages/AgentDetailPage";
import CatalogPage from "./pages/CatalogPage";
import AgentListPage from "./pages/AgentListPage";
import AdminAccessPage from "./pages/AdminAccessPage";
import AdminApprovalAuthorityPage from "./pages/AdminApprovalAuthorityPage";
import AdminGrantsPage from "./pages/AdminGrantsPage";
import AdminPublishRequestsPage from "./pages/AdminPublishRequestsPage";
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
import WorkflowsPage from "./pages/WorkflowsPage";

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
            <Routes>
              <Route path="/" element={<AgentListPage />} />
              <Route path="/agents/new" element={<CreateAgentPage />} />
              <Route path="/agents/:name" element={<AgentDetailPage />} />
              <Route path="/agents/:name/deploy" element={<DeployAgentPage />} />
              <Route path="/providers" element={<ProvidersPage />} />
              <Route path="/workflows" element={<WorkflowsPage />} />
              <Route path="/workflows/new" element={<CanvasPage />} />
              <Route path="/workflows/:id" element={<CanvasPage />} />
              <Route path="/tools" element={<ToolsPage />} />
              <Route path="/skills" element={<SkillsPage />} />
              <Route path="/catalog" element={<CatalogPage />} />
              <Route path="/admin/publish-requests" element={<AdminPublishRequestsPage />} />
              <Route path="/admin/access" element={<AdminAccessPage />} />
              <Route path="/admin/grants" element={<AdminGrantsPage />} />
              <Route path="/admin/approval-authority" element={<AdminApprovalAuthorityPage />} />
              <Route path="/hitl" element={<HITLDashboardPage />} />
              <Route path="/playground" element={<PlaygroundPage />} />
              <Route path="/playground/datasets" element={<DatasetsPage />} />
              <Route path="/playground/eval-runs/:evalRunId" element={<EvalResultsPage />} />
            </Routes>
          </main>
        </div>
        <Toaster position="top-right" richColors closeButton />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
