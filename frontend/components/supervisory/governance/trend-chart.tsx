"use client"

import * as React from "react"
import {
  TrendSeriesOut,
  TrendPointOut,
  ChangePointOut,
  TrendMetric,
} from "@/lib/types"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { formatNumber } from "@/lib/utils"
import {
  TrendingUp,
  TrendingDown,
  AlertTriangle,
  Info,
  Calendar,
  Layers,
  Activity,
} from "lucide-react"

interface TrendChartProps {
  series: TrendSeriesOut | null
  changePoints: ChangePointOut[]
  metric: TrendMetric
  onMetricChange: (metric: TrendMetric) => void
  loading?: boolean
  cohortNote?: string | null
}

export function TrendChart({
  series,
  changePoints,
  metric,
  onMetricChange,
  loading = false,
  cohortNote,
}: TrendChartProps) {
  const points = series?.points || []

  // Metric metadata
  const METRIC_INFO: Record<
    TrendMetric,
    { label: string; desc: string; color: string; stroke: string }
  > = {
    sap: {
      label: "Supervisory Action Priority (SAP)",
      desc: "Composite risk priority (0..1) driving tier assignment",
      color: "text-amber-500",
      stroke: "#f59e0b",
    },
    egi: {
      label: "Execution Gap Index (EGI)",
      desc: "Fast closures, missing escalations, monoculture notes, bunching",
      color: "text-rose-500",
      stroke: "#f43f5e",
    },
    nsi: {
      label: "Negative Space Index (NSI)",
      desc: "Silent critical assets, absent alert categories, orphan records",
      color: "text-cyan-500",
      stroke: "#06b6d4",
    },
    dts: {
      label: "Data Trust Score (DTS)",
      desc: "Schema compliance, field completeness, quarantine rate",
      color: "text-emerald-500",
      stroke: "#10b981",
    },
  }

  const currentInfo = METRIC_INFO[metric] || METRIC_INFO.sap

  // Group continuous segments to break line on gap=true
  const segments = React.useMemo(() => {
    const result: TrendPointOut[][] = []
    let currentSegment: TrendPointOut[] = []

    points.forEach((pt) => {
      if (pt.gap) {
        if (currentSegment.length > 0) {
          result.push(currentSegment)
          currentSegment = []
        }
      } else {
        currentSegment.push(pt)
      }
    })
    if (currentSegment.length > 0) {
      result.push(currentSegment)
    }
    return result
  }, [points])

  // Chart dimensions & scaling
  const chartWidth = 720
  const chartHeight = 240
  const padding = { top: 20, right: 30, bottom: 40, left: 45 }
  const innerWidth = chartWidth - padding.left - padding.right
  const innerHeight = chartHeight - padding.top - padding.bottom

  // Map points to SVG coordinates
  const nPoints = points.length
  const getX = (index: number) => {
    if (nPoints <= 1) return padding.left + innerWidth / 2
    return padding.left + (index / (nPoints - 1)) * innerWidth
  }
  const getY = (val: number | null) => {
    if (val === null || isNaN(val)) return padding.top + innerHeight / 2
    // Value is 0..1
    const clamped = Math.max(0, Math.min(1, val))
    return padding.top + (1 - clamped) * innerHeight
  }

  return (
    <div className="space-y-6">
      {/* Metric Selector Tabs */}
      <div className="flex flex-wrap items-center justify-between gap-3 bg-card p-3 rounded-lg border border-border">
        <div className="flex items-center gap-1.5">
          {(["sap", "egi", "nsi", "dts"] as TrendMetric[]).map((m) => (
            <button
              key={m}
              onClick={() => onMetricChange(m)}
              className={`px-3 py-1.5 rounded-md text-xs font-semibold tracking-wide transition-all ${
                metric === m
                  ? "bg-primary text-primary-foreground shadow-sm"
                  : "bg-muted/50 text-muted-foreground hover:bg-muted hover:text-foreground"
              }`}
            >
              {m.toUpperCase()}
            </button>
          ))}
        </div>
        <div className="text-xs text-muted-foreground flex items-center gap-2">
          <span>{currentInfo.label}</span>
          <span className="hidden sm:inline">·</span>
          <span className="text-[11px] text-muted-foreground/80 hidden sm:inline">
            {currentInfo.desc}
          </span>
        </div>
      </div>

      {/* Main Chart Area */}
      <Card className="border border-border">
        <CardHeader className="pb-2">
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2">
            <div>
              <CardTitle className="text-sm font-semibold tracking-tight text-foreground flex items-center gap-2">
                <Activity className="h-4 w-4 text-primary" />
                <span>Historical Trajectory: {series?.entity_id || "Selected Entity"}</span>
              </CardTitle>
              <CardDescription className="text-xs">
                Per-period score over successive audit windows. Line breaks represent submission gaps.
              </CardDescription>
            </div>
            {series && (
              <div className="flex items-center gap-2 text-xs">
                <Badge variant="outline" className="text-[11px] font-mono">
                  {series.n_scored} scored periods
                </Badge>
                {series.n_gaps > 0 && (
                  <Badge variant="destructive" className="text-[11px] font-mono">
                    {series.n_gaps} unsubmitted gap{series.n_gaps > 1 ? "s" : ""}
                  </Badge>
                )}
              </div>
            )}
          </div>
        </CardHeader>

        <CardContent>
          {loading ? (
            <div className="h-64 flex items-center justify-center text-xs text-muted-foreground">
              Loading trend telemetry...
            </div>
          ) : points.length === 0 ? (
            <div className="h-64 flex flex-col items-center justify-center text-xs text-muted-foreground space-y-2 border border-dashed border-border rounded-lg">
              <Layers className="h-6 w-6 opacity-40" />
              <div>No historical scoring windows found for this entity.</div>
              <div className="text-[11px]">
                Trends require at least 2 historical scoring passes over consecutive periods.
              </div>
            </div>
          ) : (
            <div className="w-full overflow-x-auto">
              <svg
                viewBox={`0 0 ${chartWidth} ${chartHeight}`}
                className="w-full h-auto min-w-[600px] select-none font-mono"
              >
                {/* Horizontal Grid lines (0.0, 0.25, 0.50, 0.75, 1.0) */}
                {[0.0, 0.25, 0.5, 0.75, 1.0].map((level) => {
                  const y = getY(level)
                  return (
                    <g key={level}>
                      <line
                        x1={padding.left}
                        y1={y}
                        x2={chartWidth - padding.right}
                        y2={y}
                        stroke="currentColor"
                        className="text-border/60"
                        strokeDasharray="3 3"
                        strokeWidth={1}
                      />
                      <text
                        x={padding.left - 8}
                        y={y + 3}
                        textAnchor="end"
                        className="text-[9px] fill-muted-foreground"
                      >
                        {level.toFixed(2)}
                      </text>
                    </g>
                  )
                })}

                {/* Discontinuous Polyline Segments */}
                {segments.map((seg, sIdx) => {
                  // Valid points with real numbers
                  const validPts = seg.filter((p) => p.value !== null && !p.gap)
                  if (validPts.length === 0) return null

                  const pathD = seg
                    .map((pt, pIdx) => {
                      const globalIdx = points.indexOf(pt)
                      const x = getX(globalIdx)
                      const y = getY(pt.value)
                      return `${pIdx === 0 ? "M" : "L"} ${x} ${y}`
                    })
                    .join(" ")

                  return (
                    <path
                      key={sIdx}
                      d={pathD}
                      fill="none"
                      stroke={currentInfo.stroke}
                      strokeWidth={2.5}
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  )
                })}

                {/* Data Points / Node Markers */}
                {points.map((pt, idx) => {
                  const x = getX(idx)
                  const y = getY(pt.value)

                  if (pt.gap) {
                    // Gap Marker: hollow red square
                    return (
                      <g key={idx}>
                        <rect
                          x={x - 4}
                          y={padding.top + innerHeight / 2 - 4}
                          width={8}
                          height={8}
                          className="stroke-rose-500 fill-card"
                          strokeWidth={1.5}
                        />
                        <text
                          x={x}
                          y={padding.top + innerHeight / 2 + 16}
                          textAnchor="middle"
                          className="text-[8px] fill-rose-500 font-bold"
                        >
                          GAP
                        </text>
                      </g>
                    )
                  }

                  if (pt.assessable === false || pt.value === null) {
                    // Un-scored marker: hollow triangle
                    return (
                      <g key={idx}>
                        <polygon
                          points={`${x},${padding.top + innerHeight / 2 - 5} ${x - 5},${
                            padding.top + innerHeight / 2 + 4
                          } ${x + 5},${padding.top + innerHeight / 2 + 4}`}
                          className="stroke-amber-500 fill-card"
                          strokeWidth={1.5}
                        />
                        <text
                          x={x}
                          y={padding.top + innerHeight / 2 + 16}
                          textAnchor="middle"
                          className="text-[8px] fill-amber-500"
                        >
                          N/A
                        </text>
                      </g>
                    )
                  }

                  return (
                    <g key={idx} className="group cursor-pointer">
                      <circle
                        cx={x}
                        cy={y}
                        r={4.5}
                        fill={currentInfo.stroke}
                        className="stroke-card stroke-2"
                      />
                      <circle
                        cx={x}
                        cy={y}
                        r={8}
                        fill="transparent"
                        className="hover:stroke-primary/40 hover:stroke-4 transition-all"
                      />
                      {/* Period Label on X Axis */}
                      <text
                        x={x}
                        y={chartHeight - padding.bottom + 16}
                        textAnchor="middle"
                        className="text-[9px] fill-muted-foreground"
                      >
                        {pt.period_end.slice(5)}
                      </text>
                      {/* Value tooltip label above point */}
                      <text
                        x={x}
                        y={y - 8}
                        textAnchor="middle"
                        className="text-[9px] font-bold fill-foreground"
                      >
                        {formatNumber(pt.value, 3)}
                      </text>
                    </g>
                  )
                })}
              </svg>
            </div>
          )}

          {/* Chart Legend & Rules Notice */}
          <div className="mt-4 pt-3 border-t border-border flex flex-wrap items-center justify-between gap-4 text-[11px] text-muted-foreground">
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-1.5">
                <span
                  className="w-2.5 h-2.5 rounded-full inline-block"
                  style={{ backgroundColor: currentInfo.stroke }}
                />
                <span>Assessed Score</span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 border border-rose-500 inline-block bg-card" />
                <span className="text-rose-500">Unsubmitted Gap (Broken Line)</span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 border border-amber-500 inline-block bg-card rotate-45" />
                <span className="text-amber-500">Not Assessable (Metric Null)</span>
              </div>
            </div>
            <div className="text-[10px] text-muted-foreground/80">
              Deterministic lineage recorded per point via run ID
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Cohort Regime Change Points Section */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
              <Calendar className="h-4 w-4 text-primary" />
              <span>Cohort Regime Change Points ({metric.toUpperCase()})</span>
            </h3>
            <p className="text-xs text-muted-foreground">
              Least-squares binary segmentation on cohort medians. Flags measured distribution shifts, not verdicts.
            </p>
          </div>
          {cohortNote && (
            <Badge variant="outline" className="text-xs text-amber-500 border-amber-500/30">
              <Info className="h-3 w-3 mr-1" /> Notice
            </Badge>
          )}
        </div>

        {cohortNote && (
          <div className="p-3 bg-muted/40 border border-border rounded-md text-xs text-muted-foreground">
            {cohortNote}
          </div>
        )}

        {changePoints.length === 0 ? (
          <div className="p-6 text-center text-xs text-muted-foreground border border-dashed border-border rounded-lg">
            No statistically significant cohort regime shifts detected for {metric.toUpperCase()} in the audited window.
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {changePoints.map((cp, idx) => {
              const isUp = cp.direction === "up"
              return (
                <Card key={idx} className="border border-border">
                  <CardContent className="p-4 space-y-3">
                    <div className="flex items-center justify-between">
                      <Badge
                        variant={isUp ? "destructive" : "success"}
                        className="text-xs font-semibold flex items-center gap-1"
                      >
                        {isUp ? (
                          <>
                            <TrendingUp className="h-3 w-3" /> Shift Up (+{cp.shift.toFixed(3)})
                          </>
                        ) : (
                          <>
                            <TrendingDown className="h-3 w-3" /> Shift Down ({cp.shift.toFixed(3)})
                          </>
                        )}
                      </Badge>
                      <span className="text-[11px] font-mono text-muted-foreground">
                        {cp.period_start} → {cp.period_end}
                      </span>
                    </div>

                    <div className="grid grid-cols-2 gap-2 text-xs pt-1 border-t border-border/60">
                      <div>
                        <div className="text-[10px] text-muted-foreground">Cohort Median Before</div>
                        <div className="font-bold text-foreground font-mono">
                          {formatNumber(cp.cohort_median_before, 3)}
                        </div>
                        <div className="text-[10px] text-muted-foreground/80">
                          {cp.n_periods_before} periods
                        </div>
                      </div>
                      <div>
                        <div className="text-[10px] text-muted-foreground">Cohort Median After</div>
                        <div className="font-bold text-foreground font-mono">
                          {formatNumber(cp.cohort_median_after, 3)}
                        </div>
                        <div className="text-[10px] text-muted-foreground/80">
                          {cp.n_periods_after} periods
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center justify-between text-[11px] pt-1 text-muted-foreground border-t border-border/60">
                      <span>Variance Reduction (SS):</span>
                      <span className="font-bold font-mono text-foreground">
                        {(cp.ss_reduction * 100).toFixed(1)}%
                      </span>
                    </div>
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
