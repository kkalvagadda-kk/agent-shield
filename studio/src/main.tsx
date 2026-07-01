import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";
import { AuthContext, buildAuthValue } from "./contexts/AuthContext";
import { initKeycloak, getParsedToken } from "./lib/keycloak";

const root = createRoot(document.getElementById("root")!);

initKeycloak()
  .then(() => {
    const user = getParsedToken();
    root.render(
      <StrictMode>
        <AuthContext.Provider value={buildAuthValue(user)}>
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
