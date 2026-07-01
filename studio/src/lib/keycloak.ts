import Keycloak from "keycloak-js";

interface KcConfig {
  keycloakUrl: string;
  keycloakRealm: string;
  keycloakClientId: string;
}

let _kc: Keycloak | null = null;

export async function initKeycloak(): Promise<Keycloak> {
  const cfg: KcConfig = await fetch("/config.json").then((r) => r.json());

  _kc = new Keycloak({
    url: cfg.keycloakUrl || window.location.origin,
    realm: cfg.keycloakRealm,
    clientId: cfg.keycloakClientId,
  });

  await _kc.init({
    onLoad: "login-required",
    pkceMethod: "S256",
    checkLoginIframe: false,
  });

  // Proactively refresh the token 30 s before it expires
  setInterval(() => {
    _kc?.updateToken(30).catch(() => {
      _kc?.logout();
    });
  }, 60_000);

  return _kc;
}

export function getKeycloak(): Keycloak | null {
  return _kc;
}

export interface KcUserInfo {
  sub: string;
  preferred_username: string;
  email?: string;
  given_name?: string;
  family_name?: string;
  realm_access?: { roles: string[] };
}

export function getParsedToken(): KcUserInfo | null {
  if (!_kc?.tokenParsed) return null;
  return _kc.tokenParsed as KcUserInfo;
}
