"use client"

import * as React from "react"
import { useEntity } from "@/lib/entity-context"
import { api, ApiError } from "@/lib/api"
import { EntityListItem, EntitySummaryOut } from "@/lib/types"
import { ScoreCards } from "@/components/supervisory/executive/score-cards"
import { PeerRadar } from "@/components/supervisory/executive/peer-radar"
import { Leaderboard } from "@/components/supervisory/executive/leaderboard"
import { Skeleton } from "@/components/ui/skeleton"
import { Button } from "@/components/ui/button"
import { AlertCircle, RefreshCw, ArrowUpRight } from "lucide-react"
import Link from "next/link"

export default function DashboardPage() {
  const { activeEntityId, setActiveEntityId } = useEntity()
  const [entities, setEntities] = React.useState<EntityListItem[]>([])
  const [summary, setSummary] = React.useState<EntitySummaryOut | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)

  const fetchData = React.useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const entityList = await api.getEntities()
      const safeEntities = Array.isArray(entityList) ? entityList : []
      setEntities(safeEntities)

      const targetId = activeEntityId || (safeEntities.length > 0 ? safeEntities[0].id : "")
      if (targetId) {
        if (!activeEntityId) setActiveEntityId(targetId)
        const entitySummary = await api.getEntitySummary(targetId)
        setSummary(entitySummary)
      }
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setError(err.message + (err.detail ? ` (${err.detail})` : ""))
      } else {
        setError("Failed to load supervisory metrics from backend.")
      }
    } finally {
      setLoading(false)
    }
  }, [activeEntityId, setActiveEntityId])

  React.useEffect(() => {
    fetchData()
  }, [fetchData])

  // Reload summary when activeEntityId changes
  React.useEffect(() => {
    if (activeEntityId) {
      api.getEntitySummary(activeEntityId)
        .then((s) => setSummary(s))
        .catch((err) => {
          console.error("Failed to load entity summary", err)
        })
    }
  }, [activeEntityId])

  if (loading && entities.length === 0) {
    return (
      <div className="space-y-6 font-sans">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <Skeleton className="h-28 rounded-lg" />
          <Skeleton className="h-28 rounded-lg" />
          <Skeleton className="h-28 rounded-lg" />
          <Skeleton className="h-28 rounded-lg" />
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          <Skeleton className="lg:col-span-6 h-[380px] rounded-lg" />
          <Skeleton className="lg:col-span-6 h-[380px] rounded-lg" />
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6 font-sans">
      {/* Backend Offline / Diagnostic Warning Banner */}
      {error && (
        <div className="border border-amber-500/30 bg-amber-500/10 p-4 rounded-lg text-xs text-amber-600 dark:text-amber-400 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <AlertCircle className="h-4 w-4 shrink-0" />
            <div>
              <div className="font-bold">Backend Diagnostic Notice</div>
              <div className="text-xs text-muted-foreground mt-0.5">{error}</div>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <Button variant="outline" size="xs" onClick={fetchData}>
              <RefreshCw className="h-3 w-3 mr-1" /> Retry Connection
            </Button>
          </div>
        </div>
      )}

      {/* Header section */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between pb-2 border-b border-border/60 gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-foreground">
            Executive Supervisory Console
          </h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Statistical anomaly surveillance for National Critical Sector Entities
          </p>
        </div>
        {summary && (
          <div className="flex items-center gap-3">
            <Link href="/review-packs">
              <Button variant="default" size="sm">
                Generate Review Pack <ArrowUpRight className="h-3.5 w-3.5 ml-1" />
              </Button>
            </Link>
          </div>
        )}
      </div>

      {/* Top Metric Cards */}
      <ScoreCards
        egi={summary?.egi ?? null}
        nsi={summary?.nsi ?? null}
        dts={summary?.dts ?? null}
        sapTier={summary?.sap_tier || "NOT_ASSESSABLE"}
        rankInterval={summary?.sap_rank_interval || { rank: 0, low: 0, high: 0 }}
        sector={summary?.sector}
        socModel={summary?.soc_model}
      />

      {/* Grid: 8-Dimension Radar & Entity Overview */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        <div className="lg:col-span-6">
          <PeerRadar
            dimensions={summary?.dimensions || []}
            entityName={summary?.name || activeEntityId}
          />
        </div>

        <div className="lg:col-span-6 flex flex-col">
          <div className="cf-card p-5 flex-1 flex flex-col justify-between rounded-lg">
            <div className="space-y-3">
              <div className="flex items-center justify-between border-b border-border pb-2">
                <span className="text-xs font-semibold text-muted-foreground">
                  Supervisory Surveillance Context
                </span>
                <span className="text-xs text-primary font-semibold">
                  Period: {summary?.period_start || "2026-09-01"} to {summary?.period_end || "2026-09-30"}
                </span>
              </div>

              <div className="text-xs leading-relaxed text-muted-foreground">
                Orion synthesizes observable procedural execution indicators against leave-one-out
                cohort baselines with empirical Bayes shrinkage. Dimensions marked{" "}
                <span className="text-rose-500 font-semibold">Not Assessable</span> reflect
                omitted submission schemas rather than verified compliance.
              </div>

              <div className="grid grid-cols-2 gap-3 pt-2">
                <div className="border border-border p-3 rounded-lg bg-muted/20">
                  <div className="text-xs text-muted-foreground">
                    Supervised CSE
                  </div>
                  <div className="text-sm font-semibold text-foreground mt-0.5 truncate">
                    {summary?.name || "No Entity Selected"}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    ID: {summary?.entity_id || "N/A"}
                  </div>
                </div>

                <div className="border border-border p-3 rounded-lg bg-muted/20">
                  <div className="text-xs text-muted-foreground">
                    Operating Architecture
                  </div>
                  <div className="text-sm font-semibold text-foreground mt-0.5 capitalize">
                    {summary?.soc_model || "In-house"} SOC
                  </div>
                  <div className="text-xs text-muted-foreground capitalize">
                    Tier: {summary?.size_tier || "Medium"} Scale
                  </div>
                </div>
              </div>
            </div>

            <div className="mt-4 pt-3 border-t border-border flex items-center justify-between text-xs">
              <span className="text-muted-foreground">Audit Lead Status:</span>
              <Link href={`/findings?entity_id=${encodeURIComponent(summary?.entity_id || "")}`}>
                <Button variant="outline" size="xs">
                  Inspect Finding Cards
                </Button>
              </Link>
            </div>
          </div>
        </div>
      </div>

      {/* Regulated CSE Cohort Leaderboard */}
      <div className="space-y-2 pt-2">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-foreground">
            CSE Regulated Cohort Leaderboard
          </h2>
        </div>
        <Leaderboard
          entities={entities}
          activeEntityId={activeEntityId}
          onSelectEntity={(id) => setActiveEntityId(id)}
        />
      </div>
    </div>
  )
}
