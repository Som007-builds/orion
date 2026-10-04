"use client"

import * as React from "react"
import { api, ApiError } from "@/lib/api"
import { PolicyProfileOut, Role } from "@/lib/types"
import { getActiveRole } from "@/lib/rbac"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { toast } from "sonner"
import {
  FileText,
  ShieldCheck,
  CheckCircle,
  Sliders,
  Calendar,
  Lock,
  RefreshCw,
  Play,
} from "lucide-react"

export default function PolicyGovernancePage() {
  const [activeProfile, setActiveProfile] = React.useState<PolicyProfileOut | null>(null)
  const [profiles, setProfiles] = React.useState<PolicyProfileOut[]>([])
  const [role, setRole] = React.useState<Role>("supervisor")
  const [loading, setLoading] = React.useState(true)
  const [activatingId, setActivatingId] = React.useState<string | null>(null)

  const loadData = React.useCallback(async () => {
    setLoading(true)
    try {
      const [active, list] = await Promise.all([
        api.getActivePolicyProfile().catch(() => null),
        api.getPolicyProfiles().catch(() => []),
      ])
      setActiveProfile(active)
      setProfiles(Array.isArray(list) ? list : [])
    } catch (err) {
      console.error("Failed to load policy profiles", err)
    } finally {
      setLoading(false)
    }
  }, [])

  React.useEffect(() => {
    setRole(getActiveRole())
    loadData()
  }, [loadData])

  const handleActivate = async (profileId: string) => {
    setActivatingId(profileId)
    try {
      const res = await api.activatePolicyProfile(profileId, {
        notes: `Activated via Supervisory Enclave UI by ${role}`,
      })
      toast.success("Policy Profile Activated", {
        description: `Active version: ${res.version} (Ledger: ${res.ledger_entry_hash.slice(0, 12)}...)`,
      })
      await loadData()
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? err.detail || err.message : "Activation failed"
      toast.error("Activation Failed", { description: msg })
    } finally {
      setActivatingId(null)
    }
  }

  const isAdmin = role === "administrator"

  return (
    <div className="space-y-6 font-sans">
      {/* Title */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between pb-2 border-b border-border/60 gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-foreground">
            Versioned Policy Profiles Governance
          </h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Phase 2.7: Machine-enforced supervisory thresholds, signed profiles, and ledger-recorded activation
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={loadData} disabled={loading} className="text-xs">
          <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {/* Active Profile Banner Card */}
      <Card className="border-l-4 border-l-primary">
        <CardHeader className="pb-2">
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2">
            <div>
              <CardTitle className="text-sm font-semibold tracking-tight text-foreground flex items-center gap-2">
                <ShieldCheck className="h-4 w-4 text-primary" />
                <span>Active Enclave Policy: {activeProfile?.profile_id || "policy_nciipc_default"}</span>
              </CardTitle>
              <CardDescription className="text-xs">
                Version {activeProfile?.version || "1.0.0"} · Effective from {activeProfile?.effective_from || "System Initialization"}
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <Badge variant="success" className="text-xs flex items-center gap-1">
                <CheckCircle className="h-3 w-3" /> Active Policy of Record
              </Badge>
            </div>
          </div>
        </CardHeader>

        <CardContent className="space-y-4">
          {loading && !activeProfile ? (
            <Skeleton className="h-28 rounded-md" />
          ) : (
            <>
              {/* Thresholds Grid from active profile summary */}
              <div>
                <div className="text-xs font-semibold text-muted-foreground mb-2 flex items-center gap-1.5">
                  <Sliders className="h-3.5 w-3.5" />
                  <span>Enclave Hypothesis Thresholds (Demoted from v1 Hardcodes to Policy Parameters)</span>
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 text-xs">
                  <div className="p-2.5 rounded-md border border-border bg-card">
                    <div className="text-[10px] text-muted-foreground">Fast Closure Limit</div>
                    <div className="text-sm font-bold font-mono text-foreground mt-0.5">
                      {String((activeProfile?.summary as any)?.fast_closure_seconds ?? "180")}s
                    </div>
                    <div className="text-[9px] text-muted-foreground/80 mt-0.5">vs peer/self baseline</div>
                  </div>

                  <div className="p-2.5 rounded-md border border-border bg-card">
                    <div className="text-[10px] text-muted-foreground">Note Min Words</div>
                    <div className="text-sm font-bold font-mono text-foreground mt-0.5">
                      {String((activeProfile?.summary as any)?.note_min_words ?? "20")} words
                    </div>
                    <div className="text-[9px] text-muted-foreground/80 mt-0.5">substance floor</div>
                  </div>

                  <div className="p-2.5 rounded-md border border-border bg-card">
                    <div className="text-[10px] text-muted-foreground">Max Closures / 30m</div>
                    <div className="text-sm font-bold font-mono text-foreground mt-0.5">
                      {String((activeProfile?.summary as any)?.max_closures_30min ?? "25")}
                    </div>
                    <div className="text-[9px] text-muted-foreground/80 mt-0.5">throughput vs human cap</div>
                  </div>

                  <div className="p-2.5 rounded-md border border-border bg-card">
                    <div className="text-[10px] text-muted-foreground">Cluster Similarity</div>
                    <div className="text-sm font-bold font-mono text-foreground mt-0.5">
                      {String((activeProfile?.summary as any)?.cluster_similarity ?? "0.88")}
                    </div>
                    <div className="text-[9px] text-muted-foreground/80 mt-0.5">MinHash-LSH threshold</div>
                  </div>

                  <div className="p-2.5 rounded-md border border-border bg-card">
                    <div className="text-[10px] text-muted-foreground">Repeat Cases</div>
                    <div className="text-sm font-bold font-mono text-foreground mt-0.5">
                      &gt;{String((activeProfile?.summary as any)?.repeat_count ?? "5")} / {String((activeProfile?.summary as any)?.repeat_window_days ?? "14")}d
                    </div>
                    <div className="text-[9px] text-muted-foreground/80 mt-0.5">recurrence cadence</div>
                  </div>

                  <div className="p-2.5 rounded-md border border-border bg-card">
                    <div className="text-[10px] text-muted-foreground">Min Measurable Share</div>
                    <div className="text-sm font-bold font-mono text-foreground mt-0.5">
                      {String((activeProfile?.summary as any)?.min_measurable_share ?? "0.50")}
                    </div>
                    <div className="text-[9px] text-muted-foreground/80 mt-0.5">prevents premature grading</div>
                  </div>
                </div>
              </div>

              {/* Hashes and Provenance */}
              <div className="p-3 bg-muted/30 border border-border rounded-md text-[11px] font-mono text-muted-foreground flex flex-col sm:flex-row justify-between gap-2">
                <div>
                  <span className="text-muted-foreground">Content Hash: </span>
                  <span className="text-foreground">{activeProfile?.content_hash || "sha256:default_active"}</span>
                </div>
                <div>
                  <span className="text-muted-foreground">Signature: </span>
                  <span className="text-foreground">{activeProfile?.signature || "ed25519:signed_host"}</span>
                </div>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {/* Profiles Catalogue / Directory */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-foreground flex items-center gap-1.5">
            <FileText className="h-4 w-4 text-primary" />
            <span>Available Policy Profiles Directory</span>
          </h2>
          <span className="text-xs text-muted-foreground">
            {!isAdmin ? "Read-Only (Administrator role required to activate)" : "Administrator Mode"}
          </span>
        </div>

        {loading && profiles.length === 0 ? (
          <Skeleton className="h-32 rounded-lg" />
        ) : profiles.length === 0 ? (
          <div className="p-6 text-center text-xs text-muted-foreground border border-dashed border-border rounded-lg">
            Default policy profile active. Additional profiles can be placed in backend/data/policies/*.yaml
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {profiles.map((p) => {
              const isActive = p.active || p.profile_id === activeProfile?.profile_id
              return (
                <Card key={p.profile_id} className={`border ${isActive ? "border-primary/60" : "border-border"}`}>
                  <CardContent className="p-4 space-y-3 text-xs">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <div className="font-bold text-foreground font-mono">{p.profile_id}</div>
                        <div className="text-[11px] text-muted-foreground mt-0.5">
                          v{p.version} · Effective: {p.effective_from}
                        </div>
                      </div>
                      <Badge variant={isActive ? "success" : "outline"} className="text-[10px]">
                        {isActive ? "Active" : "Archived"}
                      </Badge>
                    </div>

                    <div className="text-[11px] font-mono text-muted-foreground truncate border-t border-border pt-2">
                      Hash: {p.content_hash}
                    </div>

                    {isAdmin && !isActive && (
                      <Button
                        variant="outline"
                        size="xs"
                        disabled={activatingId === p.profile_id}
                        onClick={() => handleActivate(p.profile_id)}
                        className="w-full mt-1 text-xs"
                      >
                        <Play className="h-3 w-3 mr-1 text-primary" />
                        {activatingId === p.profile_id ? "Activating..." : "Activate Policy Profile"}
                      </Button>
                    )}
                  </CardContent>
                </Card>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
