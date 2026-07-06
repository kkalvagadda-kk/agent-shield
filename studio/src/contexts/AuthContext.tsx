import { createContext, useContext } from "react";
import type { KcUserInfo } from "../lib/keycloak";
import { getKeycloak } from "../lib/keycloak";

interface AuthContextValue {
  user: KcUserInfo | null;
  token: string | undefined;
  team: string | null;
  role: string | null;
  logout: () => void;
  hasRole: (role: string) => boolean;
}

export const AuthContext = createContext<AuthContextValue>({
  user: null,
  token: undefined,
  team: null,
  role: null,
  logout: () => {},
  hasRole: () => false,
});

export function useAuth() {
  return useContext(AuthContext);
}

export function buildAuthValue(
  user: KcUserInfo | null,
  team?: string | null,
  role?: string | null,
): AuthContextValue {
  const kc = getKeycloak();
  return {
    user,
    token: kc?.token,
    team: team ?? null,
    role: role ?? null,
    logout: () => kc?.logout({ redirectUri: window.location.origin }),
    hasRole: (r: string) =>
      user?.realm_access?.roles?.includes(r) ?? false,
  };
}
