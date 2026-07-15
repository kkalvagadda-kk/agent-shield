import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";
import { AuthContext, buildAuthValue } from "./contexts/AuthContext";
import { initKeycloak, getParsedToken } from "./lib/keycloak";
import { getMe } from "./api/registryApi";
import { DEMO, MOCK_USER, installFetchShim } from "./demo/demo";

// @ts-expect-error build version marker
window.__STUDIO_BUILD = "0.1.76";
const root = createRoot(document.getElementById("root")!);

// ── UX-preview mode: no Keycloak, no backend. Render with a mock user. ──
if (DEMO) {
  installFetchShim();
  root.render(
    <StrictMode>
      <AuthContext.Provider value={buildAuthValue(MOCK_USER, "demo-team", "platform-admin")}>
        <App />
      </AuthContext.Provider>
    </StrictMode>,
  );
} else {
initKeycloak()
  .then(async () => {
    const user = getParsedToken();
    let team: string | null = null;
    let role: string | null = null;
    try {
      const me = await getMe();
      team = me.team;
      role = me.role;
    } catch (err) {
      console.warn("Failed to fetch /me (team will be null):", err);
    }
    root.render(
      <StrictMode>
        <AuthContext.Provider value={buildAuthValue(user, team, role)}>
          <App />
        </AuthContext.Provider>
      </StrictMode>
    );
  })
  .catch((err) => {
    console.error("Keycloak init failed:", err);
    root.render(
      <div style={{ padding: 32, fontFamily: "sans-serif" }}>
        <h2>Authentication unavailable</h2>
        <p>
          Could not connect to the identity provider. Check that Keycloak is
          reachable and the Studio is configured correctly.
        </p>
        <pre style={{ color: "red" }}>{String(err)}</pre>
      </div>
    );
  });
}
