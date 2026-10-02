import * as React from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { HTEstimate } from "@/lib/types"
import { formatPercent, formatNumber } from "@/lib/utils"
import { Calculator } from "lucide-react"

interface PrevalencePanelProps {
  htEstimate?: HTEstimate | null
  itemsCount: number
}

export function PrevalencePanel({ htEstimate, itemsCount }: PrevalencePanelProps) {
  if (!htEstimate) {
    return (
      <Card className="border-dashed">
        <CardContent className="p-4 text-center text-xs font-sans text-muted-foreground">
          Horvitz-Thompson design-unbiased prevalence calibration will compute upon pack generation.
        </CardContent>
      </Card>
    )
  }

  const ciLowerPercent = Math.max(0, htEstimate.ci_lower * 100)
  const ciUpperPercent = Math.min(100, htEstimate.ci_upper * 100)
  const estPercent = htEstimate.estimate * 100

  return (
    <Card className="border-l-4 border-l-primary font-sans">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Calculator className="h-4 w-4 text-primary" />
            <CardTitle className="text-xs font-semibold text-foreground">
              Horvitz-Thompson Population Prevalence Estimate
            </CardTitle>
          </div>
          <span className="text-xs text-muted-foreground">
            Sample Size: {itemsCount} cases
          </span>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 pt-1">
        <div className="flex flex-col sm:flex-row items-start sm:items-baseline justify-between gap-2">
          <div>
            <div className="text-2xl font-bold text-foreground">
              {formatPercent(htEstimate.estimate)}
            </div>
            <div className="text-xs text-muted-foreground mt-0.5">
              Design-Unbiased Procedural Gap Prevalence
            </div>
          </div>
          <div className="text-right text-xs">
            <span className="text-muted-foreground">95% Confidence Interval: </span>
            <span className="font-bold text-primary">
              [{formatPercent(htEstimate.ci_lower)} - {formatPercent(htEstimate.ci_upper)}]
            </span>
          </div>
        </div>

        {/* Visual Confidence Interval Range Bar */}
        <div className="space-y-1">
          <div className="relative w-full h-3 bg-muted rounded-full overflow-hidden">
            {/* CI range highlight */}
            <div
              className="absolute top-0 bottom-0 bg-primary/20 border-l border-r border-primary/40 rounded-full"
              style={{
                left: `${ciLowerPercent}%`,
                width: `${Math.max(2, ciUpperPercent - ciLowerPercent)}%`,
              }}
            />
            {/* Point estimate marker */}
            <div
              className="absolute top-0 bottom-0 w-1.5 bg-primary rounded-full"
              style={{ left: `${estPercent}%` }}
            />
          </div>
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>0%</span>
            <span>Est: {formatPercent(htEstimate.estimate)} (SE: {formatNumber(htEstimate.standard_error, 4)})</span>
            <span>100%</span>
          </div>
        </div>

        <div className="text-xs text-muted-foreground border-t border-border pt-2 leading-relaxed">
          Inverse-probability weighted estimator. Variance accounts for systematic PPS targeted sampling and stratified control slice.
        </div>
      </CardContent>
    </Card>
  )
}
