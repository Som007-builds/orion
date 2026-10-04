"use client"

import * as React from "react"
import { useEntity } from "@/lib/entity-context"
import { api, ApiError } from "@/lib/api"
import {
  SubmissionOut,
  AssessabilityReportOut,
  DIMENSION_LABELS,
  DQReportOut,
  MappingOut,
  QuarantineRowOut,
  Role,
} from "@/lib/types"
import { formatPercent } from "@/lib/utils"
import { getActiveRole, ROLES } from "@/lib/rbac"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { toast } from "sonner"
import {
  FileSpreadsheet,
  RefreshCw,
  Sliders,
  CheckCircle,
  AlertTriangle,
  Upload,
  Database,
  ExternalLink,
  X,
  FileCheck,
  ShieldAlert,
} from "lucide-react"

export default function SubmissionsPage() {
  const { activeEntityId } = useEntity()
  const [submissions, setSubmissions] = React.useState<SubmissionOut[]>([])
  const [assessability, setAssessability] = React.useState<AssessabilityReportOut | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)
  const [role, setRole] = React.useState<Role>("supervisor")

  // Detail Modal / Drawer state
  const [selectedSubId, setSelectedSubId] = React.useState<string | null>(null)
  const [dqReport, setDqReport] = React.useState<DQReportOut | null>(null)
  const [mapping, setMapping] = React.useState<MappingOut | null>(null)
  const [quarantineRows, setQuarantineRows] = React.useState<QuarantineRowOut[]>([])
  const [detailLoading, setDetailLoading] = React.useState(false)
  const [isApproving, setIsApproving] = React.useState(false)
  const [isSeeding, setIsSeeding] = React.useState(false)

  React.useEffect(() => {
    setRole(getActiveRole())
    const handleRoleChanged = () => setRole(getActiveRole())
    window.addEventListener("orion:role-changed", handleRoleChanged)
    return () => window.removeEventListener("orion:role-changed", handleRoleChanged)
  }, [])

  const loadData = React.useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const subs = await api.getSubmissions(activeEntityId || undefined)
      const safeSubs = Array.isArray(subs) ? subs : []
      setSubmissions(safeSubs)

      if (activeEntityId) {
        try {
          const report = await api.getEntityAssessability(activeEntityId)
          setAssessability(report)
        } catch {
          // Assessability report might not be generated yet
        }
      }
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setError(err.message + (err.detail ? ` (${err.detail})` : ""))
      } else {
        setError("Failed to load submissions and assessability telemetry.")
      }
    } finally {
      setLoading(false)
    }
  }, [activeEntityId])

  React.useEffect(() => {
    loadData()
  }, [loadData])

  const handleSelectSubmission = async (subId: string) => {
    setSelectedSubId(subId)
    setDetailLoading(true)
    try {
      const [dq, mapRes, qRes] = await Promise.all([
        api.getSubmissionDQ(subId).catch(() => null),
        api.getSubmissionMapping(subId).catch(() => null),
        api.getSubmissionQuarantine(subId).catch(() => ({ items: [] as QuarantineRowOut[] })),
      ])
      setDqReport(dq)
      setMapping(mapRes)
      setQuarantineRows(Array.isArray(qRes?.items) ? qRes.items : [])
    } catch (err) {
      console.error("Failed to load submission details", err)
    } finally {
      setDetailLoading(false)
    }
  }

  const handleApproveMapping = async () => {
    if (!selectedSubId) return
    setIsApproving(true)
    try {
      const res = await api.approveSubmissionMapping(selectedSubId, {
        profile_id: mapping?.detected_profile || "vendor_splunk_canonical",
        notes: "Approved via Orion Supervisory Enclave Console",
      })
      toast.success("Vendor Mapping Approved", {
        description: `Ledger entry hash: ${res.ledger_entry_hash.slice(0, 16)}...`,
      })
      // Reload mapping
      const updated = await api.getSubmissionMapping(selectedSubId)
      setMapping(updated)
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? err.detail || err.message : "Approval failed"
      toast.error("Mapping Approval Failed", { description: msg })
    } finally {
      setIsApproving(false)
    }
  }

  const handleTriggerSeed = async () => {
    setIsSeeding(true)
    try {
      const res = await api.seedIngest("demo_seed_v2")
      toast.success("Dataset Ingestion Initiated", {
        description: `Job ID ${res.job_id}: Processing ${res.format} telemetry`,
      })
      await loadData()
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? err.detail || err.message : "Seed failed"
      toast.error("Ingestion Trigger Failed", { description: msg })
    } finally {
      setIsSeeding(false)
    }
  }

  const getAssessabilityBadge = (status: string) => {
    switch (status) {
      case "assessable":
        return <Badge variant="assessable">Assessable</Badge>
      case "partial":
        return <Badge variant="partial">Partial Evidence</Badge>
      case "not_assessable":
      default:
        return <Badge variant="not_assessable">Not Assessable</Badge>
    }
  }

  const dimensions = Array.isArray(assessability?.dimensions) ? assessability.dimensions : []
  const safeSubmissions = Array.isArray(submissions) ? submissions : []
  const roleDef = ROLES[role] || ROLES.supervisor
  const canApprove = roleDef.canApproveMappings

  return (
    <div className="space-y-6 font-sans">
      {/* Title & Actions */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between pb-2 border-b border-border/60 gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-foreground">
            Submissions, DQ &amp; Assessability Matrix
          </h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Ingestion validation, per-dimension assessability gating, and quarantined row inspection
          </p>
        </div>
        <div className="flex items-center gap-2">
          {roleDef.canUploadSubmissions && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleTriggerSeed}
              disabled={isSeeding}
              className="text-xs"
            >
              <Database className="h-3.5 w-3.5 mr-1 text-primary" />
              {isSeeding ? "Queuing..." : "Seed Ingestion (Demo)"}
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={loadData} disabled={loading} className="text-xs">
            <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </div>

      {/* Top Assessability Matrix Card */}
      <Card className="border-l-4 border-l-primary">
        <CardHeader className="pb-2">
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2">
            <div>
              <CardTitle className="text-sm font-semibold tracking-tight text-foreground">
                8-Dimension Assessability Gate Matrix: {activeEntityId || "All Entities"}
              </CardTitle>
              <CardDescription className="text-xs">
                {assessability?.interpretation ||
                  "Rule: Unsubmitted telemetry gates dimensions to Not Assessable rather than zero risk."}
              </CardDescription>
            </div>
            {assessability && (
              <div className="flex items-center gap-2 text-xs">
                <Badge variant="assessable">{assessability.n_assessable} Assessable</Badge>
                <Badge variant="partial">{assessability.n_partial} Partial</Badge>
                <Badge variant="not_assessable">{assessability.n_not_assessable} Not Assessable</Badge>
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {loading && !assessability ? (
            <Skeleton className="h-44 rounded-lg" />
          ) : dimensions.length === 0 ? (
            <div className="p-6 text-center text-xs text-muted-foreground border border-dashed border-border rounded-lg">
              No per-dimension assessability telemetry recorded yet for {activeEntityId || "selected entity"}.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Dimension</TableHead>
                  <TableHead>Gate Status</TableHead>
                  <TableHead>Missing Fields (Omissions)</TableHead>
                  <TableHead>Probed Fields Present</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {dimensions.map((d) => (
                  <TableRow key={d.dimension}>
                    <TableCell className="font-semibold text-foreground">
                      {DIMENSION_LABELS[d.dimension] || d.dimension} ({d.dimension.toUpperCase()})
                    </TableCell>
                    <TableCell>{getAssessabilityBadge(d.assessability)}</TableCell>
                    <TableCell className="max-w-[300px]">
                      {d.missing_fields && d.missing_fields.length > 0 ? (
                        <div className="flex flex-wrap gap-1">
                          {d.missing_fields.map((mf) => (
                            <span
                              key={mf}
                              className="px-2 py-0.5 border border-rose-500/20 bg-rose-500/10 text-rose-600 dark:text-rose-400 rounded-md text-xs"
                            >
                              {mf}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span className="text-xs text-emerald-600 dark:text-emerald-400">
                          All required schema fields present
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground max-w-[280px] truncate">
                      {d.probed_fields?.join(", ") || "Standard telemetry"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Submissions Intake Log Table */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-foreground">
            Ingestion Intake Log &amp; Data Quality Reports
          </h2>
          <span className="text-xs text-muted-foreground">
            {safeSubmissions.length} batch submissions logged (Click a row to inspect DQ, Mapping, and Quarantine)
          </span>
        </div>

        {loading && safeSubmissions.length === 0 ? (
          <Skeleton className="h-44 rounded-lg" />
        ) : safeSubmissions.length === 0 ? (
          <div className="cf-card p-8 text-center text-xs text-muted-foreground space-y-2 rounded-lg">
            <FileSpreadsheet className="h-6 w-6 mx-auto opacity-50" />
            <div>No submissions recorded in the state store.</div>
            <div className="text-xs">
              Submissions can be ingested via the batch ingestion pipeline or SOCSim seed switcher above.
            </div>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Submission ID</TableHead>
                <TableHead>Target Entity</TableHead>
                <TableHead>Audit Period</TableHead>
                <TableHead>Received Timestamp</TableHead>
                <TableHead className="text-right">DQ Score</TableHead>
                <TableHead className="text-right">Quarantined Rows</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Inspect</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {safeSubmissions.map((sub) => {
                const isSelected = selectedSubId === sub.submission_id
                return (
                  <TableRow
                    key={sub.submission_id}
                    onClick={() => handleSelectSubmission(sub.submission_id)}
                    className={`cursor-pointer transition-colors ${
                      isSelected ? "bg-muted/70" : "hover:bg-muted/40"
                    }`}
                  >
                    <TableCell className="font-semibold text-foreground font-mono">
                      {sub.submission_id}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground font-mono">
                      {sub.entity_id}
                    </TableCell>
                    <TableCell className="text-xs">
                      {sub.period_start} to {sub.period_end}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {new Date(sub.received_ts).toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right font-bold text-foreground font-mono">
                      {formatPercent(sub.dq_score)}
                    </TableCell>
                    <TableCell className="text-right font-mono">
                      {sub.quarantine_count !== undefined && sub.quarantine_count > 0 ? (
                        <span className="text-rose-500 font-semibold">{sub.quarantine_count}</span>
                      ) : (
                        <span className="text-muted-foreground">0</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge variant={sub.status === "ingested" ? "success" : "secondary"}>
                        {sub.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right">
                      <Button variant="ghost" size="xs" className="text-xs">
                        <ExternalLink className="h-3 w-3" />
                      </Button>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
      </div>

      {/* Deep Inspection Panel for Selected Submission */}
      {selectedSubId && (
        <Card className="border-2 border-primary/40 shadow-md">
          <CardHeader className="pb-3 border-b border-border/70 flex flex-row items-center justify-between">
            <div>
              <CardTitle className="text-sm font-semibold tracking-tight text-foreground flex items-center gap-2">
                <FileCheck className="h-4 w-4 text-primary" />
                <span>Deep Submission Inspector: {selectedSubId}</span>
              </CardTitle>
              <CardDescription className="text-xs">
                Inspect Data Quality components, review and approve vendor mappings, and audit quarantined rows
              </CardDescription>
            </div>
            <Button variant="ghost" size="xs" onClick={() => setSelectedSubId(null)}>
              <X className="h-4 w-4" />
            </Button>
          </CardHeader>

          <CardContent className="pt-4">
            {detailLoading ? (
              <div className="p-8 text-center text-xs text-muted-foreground">
                Loading submission telemetry...
              </div>
            ) : (
              <Tabs defaultValue="dq" className="space-y-4">
                <TabsList className="grid grid-cols-3 max-w-md">
                  <TabsTrigger value="dq" className="text-xs">
                    DQ Report ({dqReport?.data_tier || "Tier"})
                  </TabsTrigger>
                  <TabsTrigger value="mapping" className="text-xs">
                    Vendor Mapping ({mapping?.approved ? "Approved" : "Pending"})
                  </TabsTrigger>
                  <TabsTrigger value="quarantine" className="text-xs">
                    Quarantine ({quarantineRows.length})
                  </TabsTrigger>
                </TabsList>

                {/* Tab 1: DQ Report */}
                <TabsContent value="dq" className="space-y-4 text-xs">
                  <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
                    <div className="p-3 rounded-md bg-muted/40 border border-border">
                      <div className="text-[10px] text-muted-foreground">Overall DQ Score</div>
                      <div className="text-lg font-bold font-mono text-foreground mt-0.5">
                        {dqReport ? formatPercent(dqReport.dq_score) : "N/A"}
                      </div>
                    </div>
                    <div className="p-3 rounded-md bg-muted/40 border border-border">
                      <div className="text-[10px] text-muted-foreground">Data Quality Tier</div>
                      <div className="text-lg font-bold text-primary mt-0.5">
                        {dqReport?.data_tier || "Tier A"}
                      </div>
                    </div>
                    <div className="p-3 rounded-md bg-muted/40 border border-border">
                      <div className="text-[10px] text-muted-foreground">Rows Loaded</div>
                      <div className="text-lg font-bold font-mono text-emerald-500 mt-0.5">
                        {dqReport?.n_rows_loaded ?? 0}
                      </div>
                    </div>
                    <div className="p-3 rounded-md bg-muted/40 border border-border">
                      <div className="text-[10px] text-muted-foreground">Rows Quarantined</div>
                      <div className="text-lg font-bold font-mono text-rose-500 mt-0.5">
                        {dqReport?.n_rows_quarantined ?? 0}
                      </div>
                    </div>
                  </div>

                  {dqReport?.interpretation && (
                    <div className="p-3 bg-muted/30 border border-border rounded-md text-muted-foreground">
                      <span className="font-semibold text-foreground">Supervisory Interpretation: </span>
                      {dqReport.interpretation}
                    </div>
                  )}

                  {dqReport?.components && dqReport.components.length > 0 && (
                    <div className="space-y-2">
                      <div className="font-semibold text-foreground">DQ Component Breakdown</div>
                      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                        {dqReport.components.map((c, i) => (
                          <div key={i} className="p-2.5 rounded border border-border bg-card">
                            <div className="flex justify-between items-center text-xs">
                              <span className="font-medium text-foreground">{c.name}</span>
                              <span className="font-mono font-bold text-primary">
                                {(c.score * 100).toFixed(0)}%
                              </span>
                            </div>
                            <div className="w-full bg-muted h-1.5 rounded-full mt-2 overflow-hidden">
                              <div
                                className="bg-primary h-full"
                                style={{ width: `${c.score * 100}%` }}
                              />
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </TabsContent>

                {/* Tab 2: Vendor Mapping */}
                <TabsContent value="mapping" className="space-y-4 text-xs">
                  <div className="flex items-center justify-between p-3 bg-muted/30 border border-border rounded-md">
                    <div>
                      <div className="font-semibold text-foreground">
                        Vendor Profile: {mapping?.vendor || mapping?.detected_profile || "Generic Ingestion"}
                      </div>
                      <div className="text-[11px] text-muted-foreground">
                        Status: {mapping?.approved ? "Approved" : "Requires Supervisor / Data Custodian Sign-Off"}
                      </div>
                    </div>
                    {canApprove && !mapping?.approved && (
                      <Button
                        size="sm"
                        disabled={isApproving}
                        onClick={handleApproveMapping}
                        className="text-xs"
                      >
                        <CheckCircle className="h-3.5 w-3.5 mr-1" />
                        {isApproving ? "Approving..." : "Approve Mapping"}
                      </Button>
                    )}
                  </div>

                  {mapping?.suggestions && mapping.suggestions.length > 0 && (
                    <div className="space-y-2">
                      <div className="font-semibold text-foreground">Fuzzy Mapping Suggestions</div>
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>Source Column</TableHead>
                            <TableHead>Suggested Canonical Target</TableHead>
                            <TableHead className="text-right">Confidence</TableHead>
                            <TableHead>Rationale</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {mapping.suggestions.map((s, idx) => (
                            <TableRow key={idx}>
                              <TableCell className="font-mono font-semibold">{s.source_column}</TableCell>
                              <TableCell className="font-mono text-primary">{s.suggested_target}</TableCell>
                              <TableCell className="text-right font-mono font-bold">
                                {(s.confidence * 100).toFixed(0)}%
                              </TableCell>
                              <TableCell className="text-muted-foreground text-xs">{s.reason || "Fuzzy string match"}</TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  )}
                </TabsContent>

                {/* Tab 3: Quarantine Rows Inspector */}
                <TabsContent value="quarantine" className="space-y-3 text-xs">
                  <div className="flex items-center justify-between">
                    <span className="text-muted-foreground">
                      Rule 4: Zero silent drops. Every rejected row is recorded with the failing pipeline stage.
                    </span>
                    <Badge variant="outline" className="font-mono">
                      {quarantineRows.length} quarantined
                    </Badge>
                  </div>

                  {quarantineRows.length === 0 ? (
                    <div className="p-6 text-center text-xs text-muted-foreground border border-dashed border-border rounded-lg">
                      No rows were quarantined during this submission ingestion pass.
                    </div>
                  ) : (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Quarantine ID</TableHead>
                          <TableHead>Failed Stage</TableHead>
                          <TableHead>Rejection Reason</TableHead>
                          <TableHead>Source File &amp; Row</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {quarantineRows.map((qr) => (
                          <TableRow key={qr.quarantine_id}>
                            <TableCell className="font-mono text-foreground">{qr.quarantine_id}</TableCell>
                            <TableCell>
                              <Badge variant="destructive" className="text-[10px] font-mono">
                                {qr.failed_stage}
                              </Badge>
                            </TableCell>
                            <TableCell className="text-rose-500 font-medium">{qr.reason}</TableCell>
                            <TableCell className="font-mono text-muted-foreground text-xs">
                              {qr.source_file || "batch"}:{qr.source_row ?? "?"}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  )}
                </TabsContent>
              </Tabs>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
