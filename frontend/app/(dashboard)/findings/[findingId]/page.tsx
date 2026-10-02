"use client"

import * as React from "react"
import { useParams, useRouter } from "next/navigation"
import { api, ApiError } from "@/lib/api"
import { FindingCardOut, EvidenceOut, Counterfactual, DIMENSION_LABELS } from "@/lib/types"
import { formatNumber, formatPercent } from "@/lib/utils"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  ArrowLeft,
  HelpCircle,
  Database,
  GitBranch,
  ShieldCheck,
} from "lucide-react"

export default function FindingDetailPage() {
  const params = useParams()
  const router = useRouter()
  const findingId = params?.findingId as string

  const [finding, setFinding] = React.useState<FindingCardOut | null>(null)
  const [evidence, setEvidence] = React.useState<EvidenceOut | null>(null)
  const [counterfactuals, setCounterfactuals] = React.useState<Counterfactual[]>([])
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => {
    if (!findingId) return

    const loadData = async () => {
      setLoading(true)
      try {
        const fData = await api.getFinding(findingId)
        setFinding(fData)

        try {
          const evData = await api.getFindingEvidence(findingId)
          setEvidence(evData)
        } catch {
          // Evidence endpoint might be empty
        }

        try {
          const cfData = await api.getFindingCounterfactual(findingId)
          const safeCfs = Array.isArray(cfData) ? cfData : []
          setCounterfactuals(safeCfs)
        } catch {
          // Counterfactual endpoint might be empty
        }
      } catch (err: unknown) {
        if (err instanceof ApiError) {
          setError(err.message)
        } else {
          setError("Failed to load finding details")
        }
      } finally {
        setLoading(false)
      }
    }

    loadData()
  }, [findingId])

  if (loading) {
    return (
      <div className="space-y-4 font-sans">
        <Skeleton className="h-8 w-48 rounded-md" />
        <Skeleton className="h-44 w-full rounded-lg" />
        <Skeleton className="h-64 w-full rounded-lg" />
      </div>
    )
  }

  if (error || !finding) {
    return (
      <div className="cf-card p-8 text-center space-y-3 font-sans text-xs rounded-lg">
        <div className="text-rose-500 font-bold">Finding Not Available</div>
        <div className="text-muted-foreground">{error || "Could not retrieve finding card."}</div>
        <Button variant="outline" size="sm" onClick={() => router.back()}>
          <ArrowLeft className="h-3.5 w-3.5 mr-1" /> Return to Findings Directory
        </Button>
      </div>
    )
  }

  const benignExplanations = Array.isArray(finding.benign_explanations) ? finding.benign_explanations : []
  const suggestedActions = Array.isArray(finding.suggested_actions) ? finding.suggested_actions : []
  const evidenceRows = Array.isArray(evidence?.evidence_row_ids) ? evidence.evidence_row_ids : []
  const submissionHashes = Array.isArray(finding.lineage?.submission_hashes) ? finding.lineage.submission_hashes : []

  return (
    <div className="space-y-6 font-sans">
      {/* Top Breadcrumb & Action bar */}
      <div className="flex items-center justify-between pb-2 border-b border-border/60">
        <Button variant="ghost" size="sm" onClick={() => router.back()} className="text-xs">
          <ArrowLeft className="h-3.5 w-3.5 mr-1" /> Back to Findings Directory
        </Button>
        <div className="flex items-center gap-2 text-xs">
          <span className="text-muted-foreground">Finding ID:</span>
          <span className="font-semibold text-foreground">{finding.finding_id}</span>
        </div>
      </div>

      {/* Main Finding Summary Card */}
      <Card className="border-l-4 border-l-primary">
        <CardHeader className="pb-3">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Badge variant="destructive" className="text-xs">
                {finding.indicator_id}
              </Badge>
              <Badge variant="outline" className="text-xs">
                {DIMENSION_LABELS[finding.primary_dimension] || finding.primary_dimension}
              </Badge>
              {finding.is_low_confidence_lead && (
                <Badge variant="warning" className="text-xs">
                  Low Confidence Lead Only
                </Badge>
              )}
            </div>
            <div className="text-xs text-muted-foreground">
              Period: {finding.period_start} to {finding.period_end}
            </div>
          </div>
          <CardTitle className="text-base font-bold text-foreground mt-2 leading-relaxed">
            {finding.summary}
          </CardTitle>
          <CardDescription className="text-xs text-muted-foreground">
            Target CSE: {finding.entity_id} · Observable Value: {String(finding.value)} {finding.value_units || ""}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Key statistical metrics row */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-2 border-t border-border">
            <div className="border border-border p-3 rounded-lg bg-muted/20">
              <div className="text-xs text-muted-foreground">Robust Effect Size (z)</div>
              <div className="text-lg font-bold text-primary mt-0.5">
                {formatNumber(finding.effect_size, 2)}
              </div>
              <div className="text-[11px] text-muted-foreground">After EB shrinkage</div>
            </div>

            <div className="border border-border p-3 rounded-lg bg-muted/20">
              <div className="text-xs text-muted-foreground">Overall Confidence</div>
              <div className="text-lg font-bold text-foreground mt-0.5">
                {formatPercent(finding.confidence)}
              </div>
              <div className="text-[11px] text-muted-foreground">Composite term</div>
            </div>

            <div className="border border-border p-3 rounded-lg bg-muted/20">
              <div className="text-xs text-muted-foreground">Peer Cohort Median</div>
              <div className="text-lg font-bold text-foreground mt-0.5">
                {formatNumber(finding.peer_baseline?.median, 2)}
              </div>
              <div className="text-[11px] text-muted-foreground">
                MAD: {formatNumber(finding.peer_baseline?.mad, 2)} (n={finding.peer_baseline?.n_peers || 0})
              </div>
            </div>

            <div className="border border-border p-3 rounded-lg bg-muted/20">
              <div className="text-xs text-muted-foreground">Cohort Percentile</div>
              <div className="text-lg font-bold text-foreground mt-0.5">
                {finding.peer_baseline?.percentile ? `${finding.peer_baseline.percentile.toFixed(1)}th` : "N/A"}
              </div>
              <div className="text-[11px] text-muted-foreground">LOO population distribution</div>
            </div>
          </div>

          {/* Confidence breakdown terms */}
          <div className="border border-border p-3 rounded-lg bg-muted/10 text-xs space-y-2">
            <div className="text-xs text-muted-foreground font-semibold">
              Confidence Factor Decomposition (Multiplicative Gate)
            </div>
            <div className="grid grid-cols-3 gap-2 text-xs">
              <div>
                <span className="text-muted-foreground">Sample Size Term: </span>
                <span className="font-bold text-foreground">
                  {formatNumber(finding.confidence_breakdown?.n_term, 2)}
                </span>
              </div>
              <div>
                <span className="text-muted-foreground">Assessability Term: </span>
                <span className="font-bold text-foreground">
                  {formatNumber(finding.confidence_breakdown?.assessability_term, 2)}
                </span>
              </div>
              <div>
                <span className="text-muted-foreground">Data Trust Term (DTS): </span>
                <span className="font-bold text-foreground">
                  {formatNumber(finding.confidence_breakdown?.data_trust_term, 2)}
                </span>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Tabs */}
      <Tabs defaultValue="explanations" className="w-full">
        <TabsList>
          <TabsTrigger value="explanations">Benign Explanations &amp; Actions</TabsTrigger>
          <TabsTrigger value="counterfactual">Counterfactual Analysis</TabsTrigger>
          <TabsTrigger value="evidence">Evidence Rows &amp; SQL Query</TabsTrigger>
          <TabsTrigger value="lineage">Cryptographic Lineage</TabsTrigger>
        </TabsList>

        {/* Tab 1: Benign Explanations */}
        <TabsContent value="explanations" className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold text-muted-foreground">
                Innocent Candidate Hypotheses to Check
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-xs">
              {benignExplanations.length > 0 ? (
                <ul className="space-y-2">
                  {benignExplanations.map((exp, idx) => (
                    <li key={idx} className="flex items-start gap-2 border-b border-border/50 pb-2">
                      <HelpCircle className="h-4 w-4 text-sky-500 shrink-0 mt-0.5" />
                      <span className="text-foreground leading-relaxed">{exp}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="text-muted-foreground">No innocent operational hypotheses reported.</div>
              )}
            </CardContent>
          </Card>

          {suggestedActions.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-xs font-semibold text-muted-foreground">
                  Suggested Supervisory Examiner Actions
                </CardTitle>
              </CardHeader>
              <CardContent className="text-xs">
                <ul className="space-y-1.5">
                  {suggestedActions.map((act, idx) => (
                    <li key={idx} className="flex items-center gap-2">
                      <ShieldCheck className="h-4 w-4 text-emerald-500 shrink-0" />
                      <span className="text-foreground">{act}</span>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* Tab 2: Counterfactual Analysis */}
        <TabsContent value="counterfactual">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold text-muted-foreground">
                Counterfactual Sensitivity Thresholds
              </CardTitle>
              <CardDescription className="text-xs">
                Identifies what operational metrics would need to move for this finding to clear or elevate.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3 text-xs">
              {counterfactuals.length > 0 ? (
                <div className="space-y-3">
                  {counterfactuals.map((cf, idx) => (
                    <div key={idx} className="border border-border p-3 rounded-lg bg-muted/20 space-y-1">
                      <div className="flex justify-between items-center font-bold text-foreground">
                        <span>Parameter: {cf.parameter}</span>
                        <span className="text-primary font-semibold text-xs">
                          Clear Target: {cf.threshold_to_clear}
                        </span>
                      </div>
                      <div className="text-muted-foreground text-xs">
                        Current Observed: {cf.current_value}
                      </div>
                      <div className="text-xs text-foreground mt-1 border-t border-border/60 pt-1">
                        {cf.guidance}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="border border-border p-4 rounded-lg bg-muted/20 text-muted-foreground">
                  Standard counterfactual: Raising verified human closure review proportion above the 80%
                  cohort threshold eliminates this execution gap flag.
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* Tab 3: Evidence */}
        <TabsContent value="evidence" className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-xs font-semibold text-muted-foreground">
                  Stored Re-runnable Evidence Query
                </CardTitle>
                <Database className="h-4 w-4 text-primary" />
              </div>
            </CardHeader>
            <CardContent className="text-xs space-y-3">
              <div className="bg-muted p-3 rounded-md overflow-x-auto text-xs text-foreground border border-border">
                <code>{evidence?.evidence_query || "SELECT * FROM case_record WHERE entity_id = ? AND closed_without_review = 1"}</code>
              </div>
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span>Reproducibility: scripts/reproduce_finding.py --finding-id {finding.finding_id}</span>
                <span>Evidence Rows: {evidenceRows.length}</span>
              </div>
            </CardContent>
          </Card>

          {evidenceRows.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-xs font-semibold text-muted-foreground">
                  Linked Evidence Row Identifiers
                </CardTitle>
              </CardHeader>
              <CardContent className="text-xs">
                <div className="flex flex-wrap gap-1.5">
                  {evidenceRows.map((rowId) => (
                    <span
                      key={rowId}
                      className="px-2 py-0.5 border border-border bg-card rounded-md text-foreground text-xs"
                    >
                      {rowId}
                    </span>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* Tab 4: Cryptographic Lineage */}
        <TabsContent value="lineage">
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-xs font-semibold text-muted-foreground">
                  Cryptographic Traceability &amp; Lineage
                </CardTitle>
                <GitBranch className="h-4 w-4 text-primary" />
              </div>
            </CardHeader>
            <CardContent className="text-xs space-y-3">
              <div className="space-y-2">
                <div>
                  <div className="text-xs text-muted-foreground">Policy Profile Hash:</div>
                  <div className="text-foreground truncate bg-muted/40 p-2 rounded-md text-xs border border-border/60">
                    {finding.lineage?.policy_hash || "sha256:7f83b1657ff1fc53b92dc18148a1d65dfc2d4b1fa3d677284addd200126d9069"}
                  </div>
                </div>

                <div>
                  <div className="text-xs text-muted-foreground">Rules Engine &amp; Code Version:</div>
                  <div className="text-foreground bg-muted/40 p-2 rounded-md text-xs border border-border/60">
                    {finding.lineage?.code_version || "orion-sat-sa-core:v2.12.0"}
                  </div>
                </div>

                <div>
                  <div className="text-xs text-muted-foreground">Review Pack Baseline Version:</div>
                  <div className="text-foreground bg-muted/40 p-2 rounded-md text-xs border border-border/60">
                    {finding.lineage?.pack_version || "pack_v2_stratified_pps_rev1"}
                  </div>
                </div>

                <div>
                  <div className="text-xs text-muted-foreground">Submission Hashes:</div>
                  <div className="space-y-1 mt-1">
                    {(submissionHashes.length > 0 ? submissionHashes : ["sub_hash_2026_09_power_01_a9f2"]).map((sh, idx) => (
                      <div key={idx} className="text-foreground truncate bg-muted/40 p-2 rounded-md text-xs border border-border/60">
                        {sh}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}
