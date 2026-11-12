import { createContext, useCallback, useContext, useMemo, useState } from "react";
import { api } from "./api";
import type { Organization } from "./types";

interface OrgContextValue {
  orgs: Organization[];
  org: Organization | null;
  orgId: string;
  setOrgId: (id: string) => void;
  refreshOrgs: () => Promise<Organization[]>;
}

const OrgContext = createContext<OrgContextValue | null>(null);
const SELECTED_ORG_KEY = "tweaker.org";

export function OrgProvider({
  initialOrgs,
  children,
}: {
  initialOrgs: Organization[];
  children: React.ReactNode;
}) {
  const [orgs, setOrgs] = useState<Organization[]>(initialOrgs);
  const stored = localStorage.getItem(SELECTED_ORG_KEY);
  const initial = initialOrgs.find((item) => item.id === stored)?.id ?? initialOrgs[0]?.id ?? "";
  const [orgId, setOrgIdState] = useState(initial);

  const setOrgId = useCallback((id: string) => {
    setOrgIdState(id);
    localStorage.setItem(SELECTED_ORG_KEY, id);
  }, []);

  const refreshOrgs = useCallback(async () => {
    const next = await api.orgs();
    setOrgs(next);
    if (next.length && !next.some((item) => item.id === orgId)) {
      setOrgId(next[0].id);
    }
    return next;
  }, [orgId, setOrgId]);

  const value = useMemo<OrgContextValue>(
    () => ({
      orgs,
      org: orgs.find((item) => item.id === orgId) ?? orgs[0] ?? null,
      orgId: orgId || orgs[0]?.id || "",
      setOrgId,
      refreshOrgs,
    }),
    [orgs, orgId, setOrgId, refreshOrgs],
  );

  return <OrgContext.Provider value={value}>{children}</OrgContext.Provider>;
}

export function useOrg(): OrgContextValue {
  const value = useContext(OrgContext);
  if (!value) throw new Error("useOrg must be used inside OrgProvider");
  return value;
}
