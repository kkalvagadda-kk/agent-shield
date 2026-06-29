import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import { Toaster } from "sonner";
import AgentDetailPage from "./pages/AgentDetailPage";
import AgentListPage from "./pages/AgentListPage";
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
import WorkflowsPage from "./pages/WorkflowsPage";
import ToolsPage from "./pages/ToolsPage";
import SkillsPage from "./pages/SkillsPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 10_000 },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <div className="min-h-screen flex flex-col">
          <nav className="h-14 bg-slate-900 flex items-center px-6 gap-6 shrink-0 shadow-md">
            <div className="flex items-center gap-2 mr-4">
              <div className="w-7 h-7 rounded-md bg-blue-500 flex items-center justify-center text-white text-xs font-bold">
                AS
              </div>
              <span className="text-white font-semibold text-base tracking-tight">
                AgentShield
              </span>
              <span className="text-slate-400 text-sm font-normal">Studio</span>
            </div>
            <NavLink
              to="/workflows"
              className={({ isActive }) =>
                `text-sm font-medium transition-colors ${isActive ? "text-white" : "text-slate-400 hover:text-slate-200"}`
              }
            >
              Workflows
            </NavLink>
            <NavLink
              to="/tools"
              className={({ isActive }) =>
                `text-sm font-medium transition-colors ${isActive ? "text-white" : "text-slate-400 hover:text-slate-200"}`
              }
            >
              Tools
            </NavLink>
            <NavLink
              to="/skills"
              className={({ isActive }) =>
                `text-sm font-medium transition-colors ${isActive ? "text-white" : "text-slate-400 hover:text-slate-200"}`
              }
            >
              Skills
            </NavLink>
            <NavLink
              to="/"
              end
              className={({ isActive }) =>
                `text-sm font-medium transition-colors ${isActive ? "text-white" : "text-slate-400 hover:text-slate-200"}`
              }
            >
              Agents
            </NavLink>
            <NavLink
              to="/providers"
              className={({ isActive }) =>
                `text-sm font-medium transition-colors ${isActive ? "text-white" : "text-slate-400 hover:text-slate-200"}`
              }
            >
              Providers
            </NavLink>
            <NavLink
              to="/playground"
              className={({ isActive }) =>
                `text-sm font-medium transition-colors ${isActive ? "text-white" : "text-slate-400 hover:text-slate-200"}`
              }
            >
              Playground
            </NavLink>
            <NavLink
              to="/playground/datasets"
              className={({ isActive }) =>
                `text-sm font-medium transition-colors ${isActive ? "text-white" : "text-slate-400 hover:text-slate-200"}`
              }
            >
              Datasets
            </NavLink>
            <div className="ml-auto flex items-center gap-4">
              <NavLink
                to="/hitl"
                className={({ isActive }) =>
                  `text-sm font-medium transition-colors ${isActive ? "text-white" : "text-slate-400 hover:text-slate-200"}`
                }
              >
                HITL Queue
              </NavLink>
              <NavLink
                to="/admin/publish-requests"
                className={({ isActive }) =>
                  `text-sm font-medium transition-colors ${isActive ? "text-white" : "text-slate-400 hover:text-slate-200"}`
                }
              >
                Publish Queue
              </NavLink>
              <NavLink
                to="/admin/grants"
                className={({ isActive }) =>
                  `text-sm font-medium transition-colors ${isActive ? "text-white" : "text-slate-400 hover:text-slate-200"}`
                }
              >
                Grants
              </NavLink>
              <NavLink
                to="/admin/approval-authority"
                className={({ isActive }) =>
                  `text-sm font-medium transition-colors ${isActive ? "text-white" : "text-slate-400 hover:text-slate-200"}`
                }
              >
                Approvers
              </NavLink>
            </div>
          </nav>
          <main className="flex-1">
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
              <Route path="/admin/publish-requests" element={<AdminPublishRequestsPage />} />
              <Route path="/admin/grants" element={<AdminGrantsPage />} />
              <Route path="/admin/approval-authority" element={<AdminApprovalAuthorityPage />} />
              {/* Phase 9.3 — HITL dashboard */}
              <Route path="/hitl" element={<HITLDashboardPage />} />
              {/* Phase 10.2 — Playground */}
              <Route path="/playground" element={<PlaygroundPage />} />
              {/* Phase 10.3 — Datasets + Eval */}
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
