"use client"

import * as React from "react"
import {
  PackOut,
  PackShadowOut,
  PackTransitionOut,
} from "@/lib/types"
import { api, ApiError } from "@/lib/api"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { toast } from "sonner"
import {
  Package,
  ShieldCheck,
  Play,
  ArrowUpCircle,
  RotateCcw,
  Plus,
  GitBranch,
  CheckCircle,
  AlertTriangle,
  FileCode,
} from "lucide-react"

interface PackAdminProps {
  canManage: boolean
}

export function PackAdmin({ canManage }: PackAdminProps) {
  // Staging form state
  const [sourcePath, setSourcePath] = React.useState("data/packs/staged/nciipc_rules_v2.14.zip")
  const [version, setVersion] = React.useState("2.14.0")
  const [signedBy, setSignedBy] = React.useState("host-enclave-key")
  const [isStaging, setIsStaging] = React.useState(false)

  // Selected pack & operation state
  const [packIdInput, setPackIdInput] = React.useState("")
  const [shadowResult, setShadowResult] = React.useState<PackShadowOut | null>(null)
  const [isShadowing, setIsShadowing] = React.useState(false)
  const [isPromoting, setIsPromoting] = React.useState(false)
  const [isRollingBack, setIsRollingBack] = React.useState(false)
  const [lastTransition, setLastTransition] = React.useState<PackTransitionOut | null>(null)
  const [stagedPacks, setStagedPacks] = React.useState<PackOut[]>([])

  const handleStagePack = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!sourcePath || !version) {
      toast.error("Source path and version are required")
      return
    }
    setIsStaging(true)
    try {
      const res = await api.stagePack({
        source_path: sourcePath,
        version: version,
        signed_by: signedBy,
      })
      toast.success("Pack Staged Successfully", {
        description: `Pack ${res.pack_id} staged with signature ${res.signature.slice(0, 16)}...`,
      })
      setStagedPacks((prev) => [res, ...prev.filter((p) => p.pack_id !== res.pack_id)])
      setPackIdInput(res.pack_id)
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? err.detail || err.message : "Failed to stage pack"
      toast.error("Staging Failed", { description: msg })
    } finally {
      setIsStaging(false)
    }
  }

  const handleRunShadow = async () => {
    if (!packIdInput) {
      toast.error("Specify a Pack ID to shadow test")
      return
    }
    setIsShadowing(true)
    setShadowResult(null)
    try {
      const res = await api.shadowPack(packIdInput)
      setShadowResult(res)
      toast.success("Shadow Run Completed", {
        description: `Recommendation: ${res.recommendation || "Completed"}`,
      })
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? err.detail || err.message : "Shadow execution failed"
      toast.error("Shadow Run Failed", { description: msg })
    } finally {
      setIsShadowing(false)
    }
  }

  const handlePromote = async () => {
    if (!packIdInput) {
      toast.error("Specify a Pack ID to promote")
      return
    }
    setIsPromoting(true)
    try {
      const res = await api.promotePack(packIdInput)
      setLastTransition(res)
      toast.success("Pack Promoted to Active", {
        description: `Active pack: ${res.pack_id} (Ledger: ${res.ledger_entry_hash.slice(0, 12)}...)`,
      })
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? err.detail || err.message : "Promotion failed"
      toast.error("Promotion Failed", { description: msg })
    } finally {
      setIsPromoting(false)
    }
  }

  const handleRollback = async () => {
    if (!packIdInput) {
      toast.error("Specify active Pack ID to rollback")
      return
    }
    setIsRollingBack(true)
    try {
      const res = await api.rollbackPack(packIdInput)
      setLastTransition(res)
      toast.success("Pack Rolled Back", {
        description: `Restored predecessor ${res.previous_id || "previous pack"}`,
      })
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? err.detail || err.message : "Rollback failed"
      toast.error("Rollback Failed", { description: msg })
    } finally {
      setIsRollingBack(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Overview Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
        <Card className="border border-border">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs text-muted-foreground font-semibold flex items-center gap-1.5">
              <Package className="h-3.5 w-3.5 text-primary" />
              <span>1. Stage Bundle</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-1">
            <div className="text-foreground font-medium">Deterministic Digest &amp; Ed25519</div>
            <p className="text-[11px] text-muted-foreground">
              Bundle copied to staged store, hashed deterministically, and signed with local host key.
            </p>
          </CardContent>
        </Card>

        <Card className="border border-border">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs text-muted-foreground font-semibold flex items-center gap-1.5">
              <Play className="h-3.5 w-3.5 text-cyan-500" />
              <span>2. Shadow Test</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-1">
            <div className="text-foreground font-medium">Zero-Side-Effect Re-Execution</div>
            <p className="text-[11px] text-muted-foreground">
              Re-executes policy over live window without persisting, diffing findings for promote/hold advice.
            </p>
          </CardContent>
        </Card>

        <Card className="border border-border">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs text-muted-foreground font-semibold flex items-center gap-1.5">
              <ShieldCheck className="h-3.5 w-3.5 text-emerald-500" />
              <span>3. Promote &amp; Rollback</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-1">
            <div className="text-foreground font-medium">Append-Only Audit Ledgering</div>
            <p className="text-[11px] text-muted-foreground">
              Promote installs active policy; rollback restores immediate predecessor one step at a time.
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Main Lifecycle Control Panels */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left: Staging Form */}
        <div className="lg:col-span-6 space-y-4">
          <Card className="border border-border">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold tracking-tight text-foreground flex items-center gap-2">
                <Plus className="h-4 w-4 text-primary" />
                <span>Stage New Rule Pack</span>
              </CardTitle>
              <CardDescription className="text-xs">
                Upload or declare a zipped rule pack bundle path on the local air-gapped host.
              </CardDescription>
            </CardHeader>

            <CardContent>
              <form onSubmit={handleStagePack} className="space-y-3 text-xs">
                <div>
                  <label className="text-muted-foreground font-medium">Source Bundle Host Path</label>
                  <input
                    type="text"
                    value={sourcePath}
                    onChange={(e) => setSourcePath(e.target.value)}
                    placeholder="e.g. data/packs/staged/pack_v2.zip"
                    disabled={!canManage || isStaging}
                    className="h-8 w-full mt-1 px-3 bg-card border border-border rounded-md focus:outline-none focus:border-primary font-mono text-[11px]"
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-muted-foreground font-medium">Pack Version</label>
                    <input
                      type="text"
                      value={version}
                      onChange={(e) => setVersion(e.target.value)}
                      placeholder="e.g. 2.14.0"
                      disabled={!canManage || isStaging}
                      className="h-8 w-full mt-1 px-3 bg-card border border-border rounded-md focus:outline-none focus:border-primary font-mono text-[11px]"
                    />
                  </div>
                  <div>
                    <label className="text-muted-foreground font-medium">Host Key Alias</label>
                    <input
                      type="text"
                      value={signedBy}
                      onChange={(e) => setSignedBy(e.target.value)}
                      placeholder="host-enclave-key"
                      disabled={!canManage || isStaging}
                      className="h-8 w-full mt-1 px-3 bg-card border border-border rounded-md focus:outline-none focus:border-primary font-mono text-[11px]"
                    />
                  </div>
                </div>

                <Button
                  type="submit"
                  disabled={!canManage || isStaging}
                  size="sm"
                  className="w-full text-xs mt-2"
                >
                  <Package className="h-3.5 w-3.5 mr-1.5" />
                  {isStaging ? "Hashing & Signing..." : "Stage & Sign Rule Pack"}
                </Button>
              </form>
            </CardContent>
          </Card>

          {/* Staged Packs Quick List */}
          {stagedPacks.length > 0 && (
            <Card className="border border-border">
              <CardHeader className="pb-2">
                <CardTitle className="text-xs font-semibold text-muted-foreground">
                  Session Staged Packs
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-xs">
                {stagedPacks.map((p) => (
                  <div
                    key={p.pack_id}
                    onClick={() => setPackIdInput(p.pack_id)}
                    className="p-2.5 rounded-md border border-border/70 hover:border-primary/50 cursor-pointer bg-card transition-all flex items-center justify-between"
                  >
                    <div>
                      <div className="font-bold font-mono text-foreground">{p.pack_id}</div>
                      <div className="text-[11px] text-muted-foreground">
                        v{p.version} · {new Date(p.staged_ts).toLocaleTimeString()}
                      </div>
                    </div>
                    <Badge variant="outline" className="text-[10px] font-mono">
                      {p.status}
                    </Badge>
                  </div>
                ))}
              </CardContent>
            </Card>
          )}
        </div>

        {/* Right: Shadow Testing & Lifecycle Execution */}
        <div className="lg:col-span-6 space-y-4">
          <Card className="border border-border">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold tracking-tight text-foreground flex items-center gap-2">
                <GitBranch className="h-4 w-4 text-cyan-500" />
                <span>Pack Verification &amp; Promotion Controls</span>
              </CardTitle>
              <CardDescription className="text-xs">
                Execute shadow runs against live telemetry before promoting to active state.
              </CardDescription>
            </CardHeader>

            <CardContent className="space-y-4 text-xs">
              <div>
                <label className="text-muted-foreground font-medium">Target Pack ID</label>
                <div className="flex items-center gap-2 mt-1">
                  <input
                    type="text"
                    value={packIdInput}
                    onChange={(e) => setPackIdInput(e.target.value)}
                    placeholder="e.g. pack-20261004-xxxx"
                    className="h-8 flex-1 px-3 bg-card border border-border rounded-md focus:outline-none focus:border-primary font-mono text-[11px]"
                  />
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!packIdInput || isShadowing}
                    onClick={handleRunShadow}
                    className="text-xs shrink-0"
                  >
                    <Play className="h-3.5 w-3.5 mr-1 text-cyan-500" />
                    {isShadowing ? "Shadowing..." : "Run Shadow"}
                  </Button>
                </div>
              </div>

              {/* Shadow Run Diff Report */}
              {shadowResult && (
                <div className="p-3.5 rounded-lg border border-border bg-muted/30 space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-foreground flex items-center gap-1.5">
                      <FileCode className="h-4 w-4 text-primary" />
                      <span>Shadow Diff Report</span>
                    </span>
                    <Badge
                      variant={
                        shadowResult.recommendation?.toLowerCase().includes("promote")
                          ? "success"
                          : "warning"
                      }
                      className="text-xs capitalize"
                    >
                      {shadowResult.recommendation || "Review"}
                    </Badge>
                  </div>

                  <div className="grid grid-cols-3 gap-2 text-center text-xs">
                    <div className="p-2 rounded bg-card border border-border">
                      <div className="text-[10px] text-muted-foreground">Added</div>
                      <div className="font-bold text-emerald-500 font-mono">
                        +{shadowResult.n_findings_added}
                      </div>
                    </div>
                    <div className="p-2 rounded bg-card border border-border">
                      <div className="text-[10px] text-muted-foreground">Changed</div>
                      <div className="font-bold text-amber-500 font-mono">
                        ~{shadowResult.n_findings_changed}
                      </div>
                    </div>
                    <div className="p-2 rounded bg-card border border-border">
                      <div className="text-[10px] text-muted-foreground">Removed</div>
                      <div className="font-bold text-rose-500 font-mono">
                        -{shadowResult.n_findings_removed}
                      </div>
                    </div>
                  </div>

                  <div className="text-[11px] text-muted-foreground font-mono truncate">
                    Ledger: {shadowResult.ledger_entry_hash}
                  </div>
                </div>
              )}

              {/* Transition Actions */}
              <div className="grid grid-cols-2 gap-3 pt-2 border-t border-border">
                <Button
                  variant="default"
                  size="sm"
                  disabled={!canManage || !packIdInput || isPromoting}
                  onClick={handlePromote}
                  className="w-full text-xs"
                >
                  <ArrowUpCircle className="h-3.5 w-3.5 mr-1.5" />
                  {isPromoting ? "Promoting..." : "Promote to Active"}
                </Button>

                <Button
                  variant="outline"
                  size="sm"
                  disabled={!canManage || !packIdInput || isRollingBack}
                  onClick={handleRollback}
                  className="w-full text-xs border-amber-500/40 text-amber-600 dark:text-amber-400 hover:bg-amber-500/10"
                >
                  <RotateCcw className="h-3.5 w-3.5 mr-1.5" />
                  {isRollingBack ? "Rolling back..." : "Rollback to Predecessor"}
                </Button>
              </div>

              {lastTransition && (
                <div className="p-3 bg-emerald-500/10 border border-emerald-500/20 text-emerald-600 dark:text-emerald-400 rounded-md text-xs space-y-1">
                  <div className="font-semibold flex items-center gap-1.5">
                    <CheckCircle className="h-3.5 w-3.5" />
                    <span>Transition Recorded ({lastTransition.status})</span>
                  </div>
                  <div className="font-mono text-[10px] text-muted-foreground">
                    Pack: {lastTransition.pack_id} · Prev: {lastTransition.previous_id || "none"}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
