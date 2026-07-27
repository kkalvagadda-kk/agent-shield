import { createContext, useContext } from "react";
import type { KcUserInfo } from "../lib/keycloak";
import { getKeycloak } from "../lib/keycloak";

type GlobalRole = "consumer" | "contributor" | "platform-admin";

const ROLE_LEVEL: Record<string, number> = {
  consumer: 0,
  contributor: 1,
  "platform-admin": 2,
  // Legacy spellings still present in un-migrated rows / in-flight JWTs.
  // Mirrors rbac._LEGACY_MAP on the backend.
  viewer: 0,
  operator: 1,
  admin: 2,
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
      const userLevel = ROLE_LEVEL[normalizedRole ?? "consumer"] ?? 0;
      const minLevel = ROLE_LEVEL[minRole] ?? 0;
      return userLevel >= minLevel;
    },
  };
}
