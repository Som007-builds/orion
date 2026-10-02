"use client"

import * as React from "react"
import Link from "next/link"
import { useEntity } from "@/lib/entity-context"
import { api, ApiError } from "@/lib/api"
import { FindingCardOut, DIMENSION_LABELS } from "@/lib/types"
import { formatNumber, formatPercent } from "@/lib/utils"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { ArrowRight, RefreshCw, AlertTriangle, Search } from "lucide-react"

export default function FindingsPage() {
  const { activeEntityId } = useEntity()
  const [findings, setFindings] = React.useState<FindingCardOut[]>([])
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)
  const [filterQuery, setFilterQuery] = React.useState("")

  const loadFindings = React.useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.getFindings(activeEntityId || undefined)
      const safeFindings = Array.isArray(data) ? data : []
      setFindings(safeFindings)
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setError(err.message + (err.detail ? ` (${err.detail})` : ""))
      } else {
        setError("Failed to fetch findings.")
      }
    } finally {
      setLoading(false)
    }
  }, [activeEntityId])

  React.useEffect(() => {
    loadFindings()
  }, [loadFindings])

  const safeFindings = Array.isArray(findings) ? findings : []

  const filteredFindings = React.useMemo(() => {
    return safeFindings.filter((f) => {
      const query = filterQuery.toLowerCase()
      return (
        (f.indicator_id || "").toLowerCase().includes(query) ||
        (f.summary || "").toLowerCase().includes(query) ||
        (f.entity_id || "").toLowerCase().includes(query) ||
        (DIMENSION_LABELS[f.primary_dimension] || "").toLowerCase().includes(query)
      )
    })
  }, [safeFindings, filterQuery])

  return (
    <div className="space-y-6 font-sans">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between pb-2 border-b border-border/60 gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-foreground">
            Supervisory Findings Directory
          </h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Statistical anomaly detections surviving Benjamini-Hochberg False Discovery Rate control
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={loadFindings} disabled={loading} className="text-xs">
            <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </div>

      {/* Filter toolbar */}
      <div className="flex items-center justify-between gap-4">
        <div className="relative w-full sm:w-80">
          <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
          <input
            type="text"
            placeholder="Search indicator, summary, dimension..."
            value={filterQuery}
            onChange={(e) => setFilterQuery(e.target.value)}
            className="h-8 w-full pl-8 pr-3 text-xs bg-card border border-border rounded-md focus:outline-none focus:border-primary placeholder:text-muted-foreground"
          />
        </div>
        <div className="text-xs text-muted-foreground hidden sm:block">
          Showing {filteredFindings.length} findings
        </div>
      </div>

      {/* Main Table */}
      {loading ? (
        <Skeleton className="h-64 rounded-lg" />
      ) : error ? (
        <div className="cf-card p-8 text-center text-xs text-amber-500 space-y-2 rounded-lg">
          <AlertTriangle className="h-5 w-5 mx-auto" />
          <div>{error}</div>
          <div className="text-xs text-muted-foreground">
            Backend may have zero findings generated yet, or backend is offline.
          </div>
        </div>
      ) : filteredFindings.length === 0 ? (
        <div className="cf-card p-12 text-center text-xs text-muted-foreground space-y-2 rounded-lg">
          <div>No supervisory findings reported for this entity and period.</div>
          <div className="text-xs">
            Findings appear when observable signals exceed robust cohort baselines.
          </div>
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Indicator ID</TableHead>
              <TableHead>Dimension</TableHead>
              <TableHead>Entity</TableHead>
              <TableHead>Finding Summary</TableHead>
              <TableHead className="text-right">Effect Size (z)</TableHead>
              <TableHead className="text-right">Confidence</TableHead>
              <TableHead>Lead Type</TableHead>
              <TableHead className="text-right">Action</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filteredFindings.map((f) => (
              <TableRow key={f.finding_id}>
                <TableCell className="font-semibold text-foreground">
                  <Badge variant="destructive" className="text-xs">
                    {f.indicator_id}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs font-medium">
                  {DIMENSION_LABELS[f.primary_dimension] || f.primary_dimension}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {f.entity_id}
                </TableCell>
                <TableCell className="max-w-[320px] truncate text-xs text-foreground">
                  {f.summary}
                </TableCell>
                <TableCell className="text-right font-semibold text-primary text-xs">
                  {formatNumber(f.effect_size, 2)}
                </TableCell>
                <TableCell className="text-right font-medium text-xs">
                  {formatPercent(f.confidence)}
                </TableCell>
                <TableCell>
                  {f.is_low_confidence_lead ? (
                    <Badge variant="warning" className="text-xs">Lead Only</Badge>
                  ) : (
                    <Badge variant="outline" className="text-xs text-emerald-600 dark:text-emerald-400">Surfaced</Badge>
                  )}
                </TableCell>
                <TableCell className="text-right">
                  <Link href={`/findings/${encodeURIComponent(f.finding_id)}`}>
                    <Button variant="outline" size="xs">
                      Inspect <ArrowRight className="h-3 w-3 ml-1" />
                    </Button>
                  </Link>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  )
}
