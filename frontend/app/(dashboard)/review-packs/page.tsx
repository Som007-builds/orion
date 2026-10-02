"use client"

import * as React from "react"
import { useEntity } from "@/lib/entity-context"
import { api, ApiError } from "@/lib/api"
import { ReviewPackOut, ReviewPackItemOut } from "@/lib/types"
import { PrevalencePanel } from "@/components/supervisory/review-pack/prevalence-panel"
import { VerdictModal } from "@/components/supervisory/review-pack/verdict-modal"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { toast } from "sonner"
import {
  RefreshCw,
  Plus,
} from "lucide-react"

export default function ReviewPacksPage() {
  const { activeEntityId } = useEntity()
  const [packs, setPacks] = React.useState<ReviewPackOut[]>([])
  const [selectedPackId, setSelectedPackId] = React.useState<string>("")
  const [currentPack, setCurrentPack] = React.useState<ReviewPackOut | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [generating, setGenerating] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  // Verdict modal state
  const [activeItemForVerdict, setActiveItemForVerdict] = React.useState<ReviewPackItemOut | null>(null)
  const [isVerdictOpen, setIsVerdictOpen] = React.useState(false)

  // Generator form controls
  const [targetedSize, setTargetedSize] = React.useState(15)
  const [controlSize, setControlSize] = React.useState(5)

  const loadPacks = React.useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.getReviewPacks()
      const safePacks = Array.isArray(data) ? data : []
      setPacks(safePacks)
      if (safePacks.length > 0) {
        const match = safePacks.find((p) => p.entity_id === activeEntityId)
        const packToSelect = match ? match.pack_id : safePacks[0].pack_id
        setSelectedPackId(packToSelect)
        const detailed = await api.getReviewPack(packToSelect)
        setCurrentPack(detailed)
      } else {
        setCurrentPack(null)
      }
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setError(err.message + (err.detail ? ` (${err.detail})` : ""))
      } else {
        setError("Failed to fetch review packs.")
      }
    } finally {
      setLoading(false)
    }
  }, [activeEntityId])

  React.useEffect(() => {
    loadPacks()
  }, [loadPacks])

  const handleSelectPack = async (packId: string) => {
    setSelectedPackId(packId)
    try {
      const detailed = await api.getReviewPack(packId)
      setCurrentPack(detailed)
    } catch {
      toast.error("Failed to load pack details")
    }
  }

  const handleGeneratePack = async () => {
    if (!activeEntityId) {
      toast.error("Please select a target CSE entity first.")
      return
    }
    setGenerating(true)
    try {
      const newPack = await api.createReviewPack({
        entity_id: activeEntityId,
        targeted_size: Number(targetedSize),
        control_size: Number(controlSize),
        seed: 42,
      })
      toast.success("Review Pack Generated", {
        description: `Created pack ${newPack.pack_id} via systematic PPS sampling.`,
      })
      await loadPacks()
      setSelectedPackId(newPack.pack_id)
      setCurrentPack(newPack)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to generate pack"
      toast.error("Pack generation failed", { description: msg })
    } finally {
      setGenerating(false)
    }
  }

  const getVerdictBadge = (verdict?: ReviewPackItemOut["verdict"]) => {
    if (!verdict) {
      return (
        <Badge variant="outline" className="text-[10px] text-muted-foreground">
          Pending
        </Badge>
      )
    }
    switch (verdict.verdict) {
      case "confirmed":
        return <Badge variant="destructive">Confirmed</Badge>
      case "benign":
        return <Badge variant="success">Benign</Badge>
      case "insufficient_information":
      default:
        return <Badge variant="warning">Inconclusive</Badge>
    }
  }

  const items = Array.isArray(currentPack?.items) ? currentPack.items : []

  return (
    <div className="space-y-6 font-sans">
      {/* Title & Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between pb-2 border-b border-border/60 gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-foreground">
            Review-Pack Workbench
          </h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Probability Proportional to Size (PPS) audit sampling with design-unbiased prevalence calibration
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={loadPacks} disabled={loading}>
            <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </div>

      {/* Top Generator Controls */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-xs font-semibold text-muted-foreground">
            Systematic PPS Pack Generator
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 sm:grid-cols-4 gap-4 items-end">
            <div>
              <label className="text-xs text-muted-foreground font-medium">
                Targeted Sample Size (PPS)
              </label>
              <input
                type="number"
                min={1}
                max={50}
                value={targetedSize}
                onChange={(e) => setTargetedSize(Number(e.target.value))}
                className="h-8 w-full mt-1 px-3 text-xs bg-card border border-border rounded-md focus:outline-none focus:border-primary"
              />
            </div>

            <div>
              <label className="text-xs text-muted-foreground font-medium">
                Control Slice Size (SRS)
              </label>
              <input
                type="number"
                min={0}
                max={20}
                value={controlSize}
                onChange={(e) => setControlSize(Number(e.target.value))}
                className="h-8 w-full mt-1 px-3 text-xs bg-card border border-border rounded-md focus:outline-none focus:border-primary"
              />
            </div>

            <div>
              <label className="text-xs text-muted-foreground font-medium">
                Fixed PRNG Seed
              </label>
              <input
                type="text"
                disabled
                value="42 (Deterministic)"
                className="h-8 w-full mt-1 px-3 text-xs bg-muted border border-border rounded-md text-muted-foreground"
              />
            </div>

            <div>
              <Button
                variant="default"
                size="sm"
                className="w-full text-xs"
                onClick={handleGeneratePack}
                disabled={generating}
              >
                <Plus className="h-3.5 w-3.5 mr-1" />
                {generating ? "Sampling..." : "Generate New Pack"}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Prevalence Panel */}
      <PrevalencePanel
        htEstimate={currentPack?.ht_prevalence}
        itemsCount={items.length}
      />

      {/* Main Pack Items Table */}
      <div className="space-y-3">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-foreground">
              Review Pack Items:
            </span>
            {packs.length > 0 && (
              <select
                value={selectedPackId}
                onChange={(e) => handleSelectPack(e.target.value)}
                className="h-8 px-2.5 text-xs bg-card border border-border rounded-md focus:outline-none focus:border-primary"
              >
                {packs.map((p) => (
                  <option key={p.pack_id} value={p.pack_id}>
                    {p.pack_id} ({p.entity_id}) · {p.items_count} cases
                  </option>
                ))}
              </select>
            )}
          </div>
          <div className="text-xs text-muted-foreground">
            Targeted cases sampled with π &gt; 0; controls sampled via SRS
          </div>
        </div>

        {loading ? (
          <Skeleton className="h-64 rounded-lg" />
        ) : items.length === 0 ? (
          <div className="cf-card p-8 text-center text-xs text-muted-foreground space-y-2 rounded-lg">
            <div>No review pack loaded or created for this session.</div>
            <div className="text-xs">
              Click &quot;Generate New Pack&quot; above to perform systematic PPS sampling on loaded CSE cases.
            </div>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Case ID</TableHead>
                <TableHead>Stratum</TableHead>
                <TableHead className="text-right">Risk Score</TableHead>
                <TableHead className="text-right">Inclusion Prob (π)</TableHead>
                <TableHead>Sampling Rationale</TableHead>
                <TableHead>Supervisory Prompts</TableHead>
                <TableHead>Verdict</TableHead>
                <TableHead className="text-right">Action</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => (
                <TableRow key={item.item_id}>
                  <TableCell className="font-semibold text-foreground">
                    {item.case_id}
                  </TableCell>
                  <TableCell>
                    <Badge
                      variant={
                        item.stratum === "critical"
                          ? "destructive"
                          : item.stratum === "high"
                          ? "warning"
                          : item.stratum === "control"
                          ? "outline"
                          : "secondary"
                      }
                      className="text-xs capitalize"
                    >
                      {item.stratum}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right font-medium">
                    {item.risk_score.toFixed(3)}
                  </TableCell>
                  <TableCell className="text-right text-primary font-semibold">
                    {item.inclusion_probability.toFixed(4)}
                  </TableCell>
                  <TableCell className="max-w-[280px] truncate text-xs text-foreground" title={item.selected_because}>
                    {item.selected_because}
                  </TableCell>
                  <TableCell className="max-w-[240px] truncate text-xs text-muted-foreground" title={item.verification_prompts?.join("; ")}>
                    {item.verification_prompts?.[0] || "Standard verification check"}
                  </TableCell>
                  <TableCell>
                    {getVerdictBadge(item.verdict)}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="outline"
                      size="xs"
                      onClick={() => {
                        setActiveItemForVerdict(item)
                        setIsVerdictOpen(true)
                      }}
                    >
                      Record Verdict
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>

      {/* Verdict Capture Dialog */}
      <VerdictModal
        item={activeItemForVerdict}
        open={isVerdictOpen}
        onClose={() => setIsVerdictOpen(false)}
        onSuccess={() => {
          if (selectedPackId) handleSelectPack(selectedPackId)
        }}
      />
    </div>
  )
}
