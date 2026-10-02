"use client"

import * as React from "react"
import {
  Radar,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  ResponsiveContainer,
  Tooltip,
} from "recharts"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { DimensionScoreOut, DIMENSION_LABELS, Dimension } from "@/lib/types"

interface PeerRadarProps {
  dimensions: DimensionScoreOut[]
  entityName?: string
}

export function PeerRadar({ dimensions, entityName = "Selected Entity" }: PeerRadarProps) {
  const safeDimensions = Array.isArray(dimensions) ? dimensions : []

  const chartData = React.useMemo(() => {
    const allDims: Dimension[] = ["td", "inv", "esc", "ir", "so", "gov", "od", "cr"]

    return allDims.map((dimKey) => {
      const found = safeDimensions.find((d) => d.dimension === dimKey)
      const isAssessable = found?.assessability === "assessable" || found?.assessability === "partial"

      return {
        dimension: dimKey.toUpperCase(),
        fullName: DIMENSION_LABELS[dimKey],
        entityScore: isAssessable && found?.score !== null ? Number(found?.score.toFixed(3)) : 0,
        peerMedian: found?.peer_median !== undefined ? Number(found.peer_median.toFixed(3)) : 0.5,
        assessability: found?.assessability || "not_assessable",
        isNotAssessable: found?.assessability === "not_assessable",
      }
    })
  }, [safeDimensions])

  const notAssessableCount = safeDimensions.filter((d) => d.assessability === "not_assessable").length

  return (
    <Card className="h-full flex flex-col">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-sm font-semibold tracking-tight text-foreground">
              8-Dimension Supervisory Radar
            </CardTitle>
            <CardDescription className="text-xs">
              Entity gap index vs Cohort peer baseline (LOO median)
            </CardDescription>
          </div>
          {notAssessableCount > 0 && (
            <Badge variant="not_assessable" className="text-xs">
              {notAssessableCount} Dimensions Unassessed
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="flex-1 flex flex-col items-center justify-center p-3 min-h-[340px]">
        <div className="w-full h-[280px]">
          <ResponsiveContainer width="100%" height="100%">
            <RadarChart cx="50%" cy="50%" outerRadius="75%" data={chartData}>
              <PolarGrid stroke="var(--border)" strokeDasharray="3 3" />
              <PolarAngleAxis
                dataKey="dimension"
                tick={{ fill: "var(--foreground)", fontSize: 11, fontFamily: "var(--font-sans)" }}
              />
              <PolarRadiusAxis
                angle={90}
                domain={[0, 1]}
                tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
              />
              <Tooltip
                content={({ active, payload }) => {
                  if (active && payload && payload.length) {
                    const data = payload[0].payload
                    return (
                      <div className="bg-card border border-border p-3 rounded-md shadow-md text-xs font-sans">
                        <div className="font-semibold text-foreground mb-1.5">
                          {data.fullName} ({data.dimension})
                        </div>
                        <div className="space-y-1">
                          <div className="flex justify-between gap-4">
                            <span className="text-primary font-semibold">{entityName}:</span>
                            <span className="font-bold">
                              {data.isNotAssessable ? "Not Assessable" : data.entityScore}
                            </span>
                          </div>
                          <div className="flex justify-between gap-4">
                            <span className="text-muted-foreground">Cohort Median:</span>
                            <span>{data.peerMedian}</span>
                          </div>
                          <div className="flex justify-between gap-4 text-xs pt-1.5 border-t border-border mt-1">
                            <span className="text-muted-foreground">Gate Status:</span>
                            <span className="capitalize">{data.assessability.replace("_", " ")}</span>
                          </div>
                        </div>
                      </div>
                    )
                  }
                  return null
                }}
              />
              <Radar
                name="Cohort Median"
                dataKey="peerMedian"
                stroke="var(--color-border-strong, #888888)"
                fill="#888888"
                fillOpacity={0.15}
                strokeDasharray="4 4"
              />
              <Radar
                name={entityName}
                dataKey="entityScore"
                stroke="var(--primary)"
                fill="var(--primary)"
                fillOpacity={0.4}
              />
            </RadarChart>
          </ResponsiveContainer>
        </div>

        {/* Legend strip */}
        <div className="flex items-center gap-6 text-xs text-muted-foreground mt-2 border-t border-border/60 pt-2 w-full justify-center">
          <div className="flex items-center gap-2">
            <span className="h-2.5 w-4 bg-primary inline-block rounded-sm" />
            <span className="text-foreground font-semibold">{entityName}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="h-0.5 w-4 border-t-2 border-dashed border-muted-foreground inline-block" />
            <span>Peer Baseline (MAD Median)</span>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
