import { createContext, useContext } from "react";
import type { KcUserInfo } from "../lib/keycloak";
import { getKeycloak } from "../lib/keycloak";

interface AuthContextValue {
  user: KcUserInfo | null;
  token: string | undefined;
  logout: () => void;
  hasRole: (role: string) => boolean;
}

export const AuthContext = createContext<AuthContextValue>({
  user: null,
  token: undefined,
  logout: () => {},
  hasRole: () => false,
});

export function useAuth() {
  return useContext(AuthContext);
}

export function buildAuthValue(user: KcUserInfo | null): AuthContextValue {
  const kc = getKeycloak();
  return {
    user,
    token: kc?.token,
    logout: () => kc?.logout({ redirectUri: window.location.origin }),
    hasRole: (role: string) =>
      user?.realm_access?.roles?.includes(role) ?? false,
  };
}
