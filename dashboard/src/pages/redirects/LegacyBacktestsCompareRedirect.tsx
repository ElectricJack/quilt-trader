import { Navigate } from "react-router-dom";

export function LegacyBacktestsCompareRedirect() {
  return <Navigate to="/algorithms" replace />;
}
