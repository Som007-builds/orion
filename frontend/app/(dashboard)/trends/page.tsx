"use client"

import * as React from "react"
import { useEntity } from "@/lib/entity-context"
import { api, ApiError } from "@/lib/api"
import { TrendSeriesOut, ChangePointsOut, TrendMetric } from "@/lib/types"
import { TrendChart } from "@/components/supervisory/governance/trend-chart"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { AlertCircle, RefreshCw } from "lucide-react"

export default function TrendsPage() {
  const { activeEntityId } = useEntity()
  const [metric, setMetric] = React.useState<TrendMetric>("sap")
  const [series, setSeries] = React.useState<TrendSeriesOut | null>(null)
  const [changePoints, setChangePoints] = React.useState<ChangePointsOut | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)

  const loadData = React.useCallback(async () => {
    if (!activeEntityId) return
    setLoading(true)
    setError(null)
    try {
      const [seriesRes, cpRes] = await Promise.all([
        api.getTrends(activeEntityId, metric).catch((err) => {
          console.warn("Trends series fetch warning:", err)
          return null
        }),
        api.getChangePoints(metric).catch((err) => {
          console.warn("Change points fetch warning:", err)
          return null
        }),
      ])
      setSeries(seriesRes)
      setChangePoints(cpRes)
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setError(err.message + (err.detail ? ` (${err.detail})` : ""))
      } else {
        setError("Failed to load historical trend telemetry.")
      }
    } finally {
      setLoading(false)
    }
  }, [activeEntityId, metric])

  React.useEffect(() => {
    loadData()
  }, [loadData])

  return (
    <div className="space-y-6 font-sans">
      {/* Title & Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between pb-2 border-b border-border/60 gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-foreground">
            Multi-Period Trends &amp; Regime Shifts
          </h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Phase 2.15: Deterministic least-squares regime segmentation and historical trajectory surveillance
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={loadData}
            disabled={loading}
            className="text-xs"
          >
            <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </div>

      {/* Diagnostic Warning if backend returned error */}
      {error && (
        <div className="border border-amber-500/30 bg-amber-500/10 p-4 rounded-lg text-xs text-amber-600 dark:text-amber-400 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <AlertCircle className="h-4 w-4 shrink-0" />
            <div>
              <div className="font-bold">Trend Telemetry Notice</div>
              <div className="text-xs text-muted-foreground mt-0.5">{error}</div>
            </div>
          </div>
          <Button variant="outline" size="xs" onClick={loadData}>
            Retry
          </Button>
        </div>
      )}

      {loading && !series ? (
        <div className="space-y-4">
          <Skeleton className="h-12 w-full rounded-md" />
          <Skeleton className="h-64 w-full rounded-lg" />
          <Skeleton className="h-32 w-full rounded-lg" />
        </div>
      ) : (
        <TrendChart
          series={series}
          changePoints={changePoints?.change_points || []}
          metric={metric}
          onMetricChange={setMetric}
          loading={loading}
          cohortNote={changePoints?.note}
        />
      )}
    </div>
  )
}
