import * as React from "react"
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
import { EntityListItem, SapTier } from "@/lib/types"
import { formatNumber, formatPercent } from "@/lib/utils"
import { ArrowRight, Search } from "lucide-react"

interface LeaderboardProps {
  entities: EntityListItem[]
  activeEntityId?: string
  onSelectEntity?: (id: string) => void
}

export function Leaderboard({ entities, activeEntityId, onSelectEntity }: LeaderboardProps) {
  const safeEntities = React.useMemo(() => {
    return Array.isArray(entities) ? entities : []
  }, [entities])

  const [searchTerm, setSearchTerm] = React.useState("")
  const [sectorFilter, setSectorFilter] = React.useState<string>("all")

  const sectors = React.useMemo(() => {
    const set = new Set<string>()
    safeEntities.forEach((e) => {
      if (e?.sector) set.add(e.sector)
    })
    return Array.from(set)
  }, [safeEntities])

  const filteredEntities = React.useMemo(() => {
    return safeEntities.filter((e) => {
      if (!e) return false
      const name = (e.name || "").toLowerCase()
      const id = (e.id || "").toLowerCase()
      const matchesSearch = name.includes(searchTerm.toLowerCase()) || id.includes(searchTerm.toLowerCase())
      const matchesSector = sectorFilter === "all" || e.sector === sectorFilter
      return matchesSearch && matchesSector
    })
  }, [safeEntities, searchTerm, sectorFilter])

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
    <div className="space-y-3 font-sans">
      {/* Controls toolbar */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-3">
        <div className="flex items-center gap-2 w-full sm:w-auto">
          <div className="relative w-full sm:w-64">
            <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
            <input
              type="text"
              placeholder="Filter by CSE name or ID..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="h-8 w-full pl-8 pr-3 text-xs bg-card border border-border rounded-md text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-primary"
            />
          </div>
          {sectors.length > 0 && (
            <select
              value={sectorFilter}
              onChange={(e) => setSectorFilter(e.target.value)}
              className="h-8 px-2.5 text-xs bg-card border border-border rounded-md text-foreground focus:outline-none focus:border-primary"
            >
              <option value="all">All Sectors</option>
              {sectors.map((sec) => (
                <option key={sec} value={sec}>
                  {sec}
                </option>
              ))}
            </select>
          )}
        </div>
        <div className="text-xs text-muted-foreground self-end sm:self-center">
          Showing {filteredEntities.length} of {safeEntities.length} regulated entities
        </div>
      </div>

      {/* Table */}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>CSE Name &amp; Sector</TableHead>
            <TableHead>Model / Size</TableHead>
            <TableHead>SAP Tier</TableHead>
            <TableHead>Rank Interval</TableHead>
            <TableHead className="text-right">EGI</TableHead>
            <TableHead className="text-right">NSI</TableHead>
            <TableHead className="text-right">Data Trust (DTS)</TableHead>
            <TableHead className="text-right">Action</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {filteredEntities.length === 0 ? (
            <TableRow>
              <TableCell colSpan={8} className="text-center py-8 text-muted-foreground">
                No regulated entities matching active filter criteria.
              </TableCell>
            </TableRow>
          ) : (
            filteredEntities.map((e) => {
              const isSelected = e.id === activeEntityId
              return (
                <TableRow
                  key={e.id}
                  className={`cursor-pointer transition-colors ${
                    isSelected ? "bg-primary/5 hover:bg-primary/10 border-l-2 border-l-primary" : ""
                  }`}
                  onClick={() => onSelectEntity && onSelectEntity(e.id)}
                >
                  <TableCell>
                    <div className="font-semibold text-foreground">{e.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {e.id} · {e.sector}
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="text-xs capitalize font-medium">{e.soc_model}</div>
                    <div className="text-xs text-muted-foreground capitalize">{e.size_tier} Tier</div>
                  </TableCell>
                  <TableCell>
                    <Badge variant={getSapBadgeVariant(e.sap_tier)}>
                      {e.sap_tier === "NOT_ASSESSABLE" ? "Not Assessable" : `Tier ${e.sap_tier}`}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-xs">
                    {e.sap_rank_interval ? (
                      <span className="bg-muted px-2 py-0.5 rounded-md text-xs font-medium">
                        #{e.sap_rank_interval.low} - #{e.sap_rank_interval.high}
                      </span>
                    ) : (
                      "N/A"
                    )}
                  </TableCell>
                  <TableCell className="text-right font-medium text-xs">
                    {formatNumber(e.egi, 3)}
                  </TableCell>
                  <TableCell className="text-right text-xs text-muted-foreground">
                    {e.nsi !== null && e.nsi !== undefined ? formatNumber(e.nsi, 3) : "Unmeasured"}
                  </TableCell>
                  <TableCell className="text-right text-xs">
                    <span
                      className={`font-semibold ${
                        e.dts >= 0.7
                          ? "text-emerald-600 dark:text-emerald-400"
                          : e.dts >= 0.4
                          ? "text-amber-600 dark:text-amber-400"
                          : "text-rose-600 dark:text-rose-400"
                      }`}
                    >
                      {formatPercent(e.dts)}
                    </span>
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant={isSelected ? "default" : "outline"}
                      size="xs"
                      onClick={(ev) => {
                        ev.stopPropagation()
                        const targetId = e.id || e.entity_id || ""
                        if (onSelectEntity) onSelectEntity(targetId)
                      }}
                    >
                      {isSelected ? "Inspecting" : "Inspect"} <ArrowRight className="h-3 w-3 ml-1" />
                    </Button>
                  </TableCell>
                </TableRow>
              )
            })
          )}
        </TableBody>
      </Table>
    </div>
  )
}
