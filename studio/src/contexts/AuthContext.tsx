import { createContext, useContext } from "react";
import type { KcUserInfo } from "../lib/keycloak";
import { getKeycloak } from "../lib/keycloak";

type GlobalRole = "viewer" | "contributor" | "platform-admin";

const ROLE_LEVEL: Record<string, number> = {
  viewer: 0,
  contributor: 1,
  "platform-admin": 2,
};

interface AuthContextValue {
  user: KcUserInfo | null;
  token: string | undefined;
  team: string | null;
  role: GlobalRole | null;
  logout: () => void;
  hasRole: (role: string) => boolean;
  isAtLeast: (minRole: GlobalRole) => boolean;
}

export const AuthContext = createContext<AuthContextValue>({
  user: null,
  token: undefined,
  team: null,
  role: null,
  logout: () => {},
  hasRole: () => false,
  isAtLeast: () => false,
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
  const normalizedRole = (role ?? null) as GlobalRole | null;
  return {
    user,
    token: kc?.token,
    team: team ?? null,
    role: normalizedRole,
    logout: () => kc?.logout({ redirectUri: window.location.origin }),
    hasRole: (r: string) =>
      user?.realm_access?.roles?.includes(r) ?? false,
    isAtLeast: (minRole: GlobalRole) => {
      const userLevel = ROLE_LEVEL[normalizedRole ?? "viewer"] ?? 0;
      const minLevel = ROLE_LEVEL[minRole] ?? 0;
      return userLevel >= minLevel;
    },
  };
}
