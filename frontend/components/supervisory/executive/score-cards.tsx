import * as React from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { formatNumber, formatPercent } from "@/lib/utils"
import { SapTier, RankInterval } from "@/lib/types"
import { AlertCircle, ShieldAlert, CheckCircle2, HelpCircle } from "lucide-react"

interface ScoreCardsProps {
  egi: number | null
  nsi: number | null
  dts: number | null
  sapTier: SapTier
  rankInterval: RankInterval
  sector?: string
  socModel?: string
}

export function ScoreCards({
  egi,
  nsi,
  dts,
  sapTier,
  rankInterval,
  sector,
  socModel,
}: ScoreCardsProps) {
  const getSapBadgeVariant = (tier: SapTier) => {
    switch (tier) {
      case "T1":
        return "destructive"
      case "T2":
        return "warning"
      case "T3":
        return "secondary"
      case "T4":
        return "success"
      case "NOT_ASSESSABLE":
      default:
        return "not_assessable"
    }
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {/* Card 1: SAP Supervisory Action Priority */}
      <Card className="border-l-4 border-l-primary">
        <CardHeader className="pb-1">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-muted-foreground">
              Supervisory Action Priority (SAP)
            </span>
            <ShieldAlert className="h-4 w-4 text-primary" />
          </div>
          <CardTitle className="text-xl font-bold flex items-center gap-2 mt-1">
            <Badge variant={getSapBadgeVariant(sapTier)} className="text-xs px-2.5 py-0.5">
              {sapTier === "NOT_ASSESSABLE" ? "Not Assessable" : `Tier ${sapTier}`}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-2">
          <div className="flex flex-col gap-1 text-xs">
            <div className="flex justify-between items-center text-muted-foreground">
              <span>Rank Interval:</span>
              <span className="text-foreground font-semibold">
                {rankInterval ? `[#${rankInterval.low} - #${rankInterval.high}]` : "Unranked"}
              </span>
            </div>
            <div className="flex justify-between items-center text-muted-foreground text-[11px]">
              <span>Peer Cohort:</span>
              <span className="text-foreground">{sector || "National Critical"} ({socModel || "In-house"})</span>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Card 2: EGI Execution Gap Index */}
      <Card>
        <CardHeader className="pb-1">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-muted-foreground">
              Execution Gap Index (EGI)
            </span>
            <AlertCircle className="h-4 w-4 text-amber-500" />
          </div>
          <CardTitle className="text-2xl font-bold text-foreground mt-1">
            {egi !== null && egi !== undefined ? formatNumber(egi, 3) : "N/A"}
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-2">
          <div className="space-y-1.5">
            <div className="w-full bg-muted h-2 rounded-full overflow-hidden">
              <div
                className="bg-primary h-full transition-all duration-500 rounded-full"
                style={{ width: `${Math.min(100, Math.max(0, (egi || 0) * 100))}%` }}
              />
            </div>
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>Observable Failure Rate</span>
              <span className="font-semibold text-foreground">{formatPercent(egi)}</span>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Card 3: NSI Negative Space Index */}
      <Card>
        <CardHeader className="pb-1">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-muted-foreground">
              Negative Space Index (NSI)
            </span>
            <HelpCircle className="h-4 w-4 text-sky-500" />
          </div>
          <CardTitle className="text-2xl font-bold text-foreground mt-1">
            {nsi !== null && nsi !== undefined ? formatNumber(nsi, 3) : "Unmeasured"}
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-2">
          <div className="space-y-1.5">
            <div className="w-full bg-muted h-2 rounded-full overflow-hidden">
              <div
                className="bg-sky-500 h-full transition-all duration-500 rounded-full"
                style={{ width: `${Math.min(100, Math.max(0, (nsi || 0) * 100))}%` }}
              />
            </div>
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>Telemetry Blindspots</span>
              <span className="font-semibold text-foreground">{nsi !== null ? formatPercent(nsi) : "Stubs Only"}</span>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Card 4: DTS Data Trust Score */}
      <Card>
        <CardHeader className="pb-1">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-muted-foreground">
              Data Trust Score (DTS)
            </span>
            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
          </div>
          <CardTitle className="text-2xl font-bold text-foreground mt-1">
            {dts !== null && dts !== undefined ? formatNumber(dts, 3) : "0.000"}
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-2">
          <div className="space-y-1.5">
            <div className="w-full bg-muted h-2 rounded-full overflow-hidden">
              <div
                className={`h-full transition-all duration-500 rounded-full ${
                  (dts || 0) > 0.7 ? "bg-emerald-500" : (dts || 0) > 0.4 ? "bg-amber-500" : "bg-rose-500"
                }`}
                style={{ width: `${Math.min(100, Math.max(0, (dts || 0) * 100))}%` }}
              />
            </div>
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>Ingestion Integrity</span>
              <span className="font-semibold text-foreground">{formatPercent(dts)}</span>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
