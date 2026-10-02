"use client"

import * as React from "react"
import { useEntity } from "@/lib/entity-context"
import { api, ApiError } from "@/lib/api"
import { SubmissionOut, AssessabilityReportOut, DIMENSION_LABELS } from "@/lib/types"
import { formatPercent } from "@/lib/utils"
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
import { FileSpreadsheet, RefreshCw } from "lucide-react"

export default function SubmissionsPage() {
  const { activeEntityId } = useEntity()
  const [submissions, setSubmissions] = React.useState<SubmissionOut[]>([])
  const [assessability, setAssessability] = React.useState<AssessabilityReportOut | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)

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

  return (
    <div className="space-y-6 font-sans">
      {/* Title */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between pb-2 border-b border-border/60 gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-foreground">
            Submissions &amp; Assessability Matrix
          </h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Ingestion validation, data quality scoring, and per-dimension telemetry presence
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={loadData} disabled={loading} className="text-xs">
          <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
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
                {assessability?.interpretation || "Rule: Unsubmitted telemetry gates dimensions to Not Assessable rather than zero risk."}
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
                    <TableCell>
                      {getAssessabilityBadge(d.assessability)}
                    </TableCell>
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
            {safeSubmissions.length} batch submissions logged
          </span>
        </div>

        {loading && safeSubmissions.length === 0 ? (
          <Skeleton className="h-44 rounded-lg" />
        ) : safeSubmissions.length === 0 ? (
          <div className="cf-card p-8 text-center text-xs text-muted-foreground space-y-2 rounded-lg">
            <FileSpreadsheet className="h-6 w-6 mx-auto opacity-50" />
            <div>No submissions recorded in the state store.</div>
            <div className="text-xs">
              Submissions can be ingested via the batch ingestion pipeline or SOCSim seed switcher.
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
                <TableHead>Ingestion Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {safeSubmissions.map((sub) => (
                <TableRow key={sub.submission_id}>
                  <TableCell className="font-semibold text-foreground">
                    {sub.submission_id}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {sub.entity_id}
                  </TableCell>
                  <TableCell className="text-xs">
                    {sub.period_start} to {sub.period_end}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {new Date(sub.received_ts).toLocaleString()}
                  </TableCell>
                  <TableCell className="text-right font-bold text-foreground">
                    {formatPercent(sub.dq_score)}
                  </TableCell>
                  <TableCell className="text-right">
                    {sub.quarantine_count !== undefined ? (
                      <span className={sub.quarantine_count > 0 ? "text-amber-500 font-semibold" : "text-muted-foreground"}>
                        {sub.quarantine_count}
                      </span>
                    ) : (
                      "0"
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant={sub.status === "ingested" ? "success" : "secondary"}>
                      {sub.status}
                    </Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
    </div>
  )
}
