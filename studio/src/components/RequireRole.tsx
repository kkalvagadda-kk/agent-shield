import { Navigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";

type GlobalRole = "viewer" | "contributor" | "platform-admin";

interface Props {
  minRole: GlobalRole;
  children: React.ReactNode;
}

export default function RequireRole({ minRole, children }: Props) {
  const { isAtLeast } = useAuth();
  if (!isAtLeast(minRole)) {
    return <Navigate to="/" replace />;
  }
  return <>{children}</>;
}
