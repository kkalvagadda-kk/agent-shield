import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import { Toaster } from "sonner";
import AgentListPage from "./pages/AgentListPage";
import CanvasPage from "./pages/CanvasPage";
import CreateAgentPage from "./pages/CreateAgentPage";
import DeployAgentPage from "./pages/DeployAgentPage";
import ProvidersPage from "./pages/ProvidersPage";

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
              to="/canvas"
              className={({ isActive }) =>
                `text-sm font-medium transition-colors ${isActive ? "text-white" : "text-slate-400 hover:text-slate-200"}`
              }
            >
              Canvas
            </NavLink>
          </nav>
          <main className="flex-1">
            <Routes>
              <Route path="/" element={<AgentListPage />} />
              <Route path="/agents/new" element={<CreateAgentPage />} />
              <Route path="/agents/:name/deploy" element={<DeployAgentPage />} />
              <Route path="/providers" element={<ProvidersPage />} />
              <Route path="/canvas" element={<CanvasPage />} />
              <Route path="/workflows/:id" element={<CanvasPage />} />
            </Routes>
          </main>
        </div>
        <Toaster position="top-right" richColors closeButton />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
