"use client"

import * as React from "react"
import { api, ApiError } from "@/lib/api"
import { getActiveRole, ROLES } from "@/lib/rbac"
import { LedgerEntryOut, LedgerVerifyOut } from "@/lib/types"
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
  ShieldCheck,
  Lock,
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
  ShieldX,
} from "lucide-react"

export default function LedgerPage() {
  const [role, setRole] = React.useState(getActiveRole())
  const [entries, setEntries] = React.useState<LedgerEntryOut[]>([])
  const [verification, setVerification] = React.useState<LedgerVerifyOut | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [verifying, setVerifying] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const roleDef = ROLES[role] || ROLES.supervisor

  React.useEffect(() => {
    const handleRoleChanged = () => {
      setRole(getActiveRole())
    }
    window.addEventListener("orion:role-changed", handleRoleChanged)
    return () => window.removeEventListener("orion:role-changed", handleRoleChanged)
  }, [])

  const loadLedger = React.useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.getLedger(100, 0)
      const safeEntries = Array.isArray(data) ? data : []
      setEntries(safeEntries)
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setError(err.message + (err.detail ? ` (${err.detail})` : ""))
      } else {
        setError("Failed to fetch ledger entries.")
      }
    } finally {
      setLoading(false)
    }
  }, [])

  React.useEffect(() => {
    if (roleDef.canViewLedger) {
      loadLedger()
    }
  }, [role, roleDef.canViewLedger, loadLedger])

  const handleVerifyChain = async () => {
    setVerifying(true)
    try {
      const result = await api.verifyLedger()
      setVerification(result)
      if (result.valid) {
        toast.success("Ledger Hash Chain Verified", {
          description: `All ${result.total_entries} entries cryptographically intact. Head: ${result.head_hash.slice(0, 16)}...`,
        })
      } else {
        toast.error("Ledger Chain Integrity Compromised!", {
          description: "Tamper detected in sequence chain.",
        })
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Verification failed"
      toast.error("Chain verification error", { description: msg })
    } finally {
      setVerifying(false)
    }
  }

  // Least-privilege guard
  if (!roleDef.canViewLedger) {
    return (
      <div className="cf-card p-12 text-center space-y-3 font-sans text-xs max-w-lg mx-auto my-12 rounded-lg">
        <ShieldX className="h-8 w-8 text-rose-500 mx-auto" />
        <div className="text-base font-bold text-foreground">Access Restricted by RBAC</div>
        <div className="text-muted-foreground leading-relaxed">
          The active persona (<span className="text-primary font-bold">{roleDef.label}</span>) does not possess
          ledger read privileges under the NCIIPC least-privilege matrix.
        </div>
        <div className="text-xs text-muted-foreground pt-2 border-t border-border">
          Switch to <span className="font-semibold text-foreground">Supervisor</span> or{" "}
          <span className="font-semibold text-foreground">Auditor</span> in the top navigation bar to audit the
          append-only ledger.
        </div>
      </div>
    )
  }

  const safeEntries = Array.isArray(entries) ? entries : []

  return (
    <div className="space-y-6 font-sans">
      {/* Title */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between pb-2 border-b border-border/60 gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-foreground">
            Cryptographic Audit Ledger
          </h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Append-only hash chain with tamper detection and mathematical non-repudiation
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={loadLedger} disabled={loading} className="text-xs">
            <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
          <Button
            variant="default"
            size="sm"
            onClick={handleVerifyChain}
            disabled={verifying}
            className="text-xs"
          >
            <ShieldCheck className="h-3.5 w-3.5 mr-1.5" />
            {verifying ? "Hashing Chain..." : "Verify Hash Chain"}
          </Button>
        </div>
      </div>

      {/* Verification status card if verified */}
      {verification && (
        <Card className={verification.valid ? "border-l-4 border-l-emerald-500" : "border-l-4 border-l-rose-500"}>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                {verification.valid ? (
                  <CheckCircle2 className="h-5 w-5 text-emerald-500" />
                ) : (
                  <AlertTriangle className="h-5 w-5 text-rose-500" />
                )}
                <CardTitle className="text-xs font-semibold text-foreground">
                  Chain Verification Result: {verification.valid ? "Valid & Untampered" : "Compromised"}
                </CardTitle>
              </div>
              <span className="text-xs text-muted-foreground">
                Verified at: {new Date(verification.verified_at).toLocaleTimeString()}
              </span>
            </div>
          </CardHeader>
          <CardContent className="text-xs space-y-1">
            <div className="text-muted-foreground">
              Total Audited Blocks: <span className="text-foreground font-bold">{verification.total_entries}</span>
            </div>
            <div className="text-xs text-muted-foreground truncate">
              Head Hash: <code className="text-primary font-bold">{verification.head_hash}</code>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Main Ledger Table */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-foreground">
            Sequential Audit Blocks ({safeEntries.length})
          </h2>
          <span className="text-xs text-muted-foreground">
            SHA-256 linked blocks (prev_hash ⟵ entry_hash)
          </span>
        </div>

        {loading ? (
          <Skeleton className="h-64 rounded-lg" />
        ) : error ? (
          <div className="cf-card p-8 text-center text-xs text-amber-500 space-y-2 rounded-lg">
            <AlertTriangle className="h-5 w-5 mx-auto" />
            <div>{error}</div>
          </div>
        ) : safeEntries.length === 0 ? (
          <div className="cf-card p-8 text-center text-xs text-muted-foreground space-y-2 rounded-lg">
            <Lock className="h-5 w-5 mx-auto opacity-50" />
            <div>Ledger currently has zero append operations recorded.</div>
            <div className="text-xs">
              Actions such as review pack generation, verdict commits, and policy updates trigger signed entries.
            </div>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-16">Seq</TableHead>
                <TableHead>Timestamp</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Attributed Actor</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Block Hash (SHA-256)</TableHead>
                <TableHead>Prev Hash</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {safeEntries.map((entry) => (
                <TableRow key={entry.seq}>
                  <TableCell className="font-bold text-foreground">
                    #{entry.seq}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {new Date(entry.timestamp).toLocaleString()}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" className="text-xs capitalize">
                      {entry.action.replace("_", " ")}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-xs text-foreground font-semibold">
                    {entry.actor}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground capitalize">
                    {entry.role || "Supervisor"}
                  </TableCell>
                  <TableCell className="text-xs max-w-[160px] truncate text-primary font-medium" title={entry.entry_hash}>
                    {entry.entry_hash}
                  </TableCell>
                  <TableCell className="text-xs max-w-[160px] truncate text-muted-foreground" title={entry.prev_hash}>
                    {entry.prev_hash}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
    </div>
  )
}
