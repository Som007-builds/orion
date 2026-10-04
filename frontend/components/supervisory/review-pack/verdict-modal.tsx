"use client"

import * as React from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { ReviewPackItemOut } from "@/lib/types"
import { api } from "@/lib/api"
import { toast } from "sonner"
import { CheckCircle, AlertTriangle, HelpCircle } from "lucide-react"

interface VerdictModalProps {
  item: ReviewPackItemOut | null
  open: boolean
  onClose: () => void
  onSuccess: () => void
}

export function VerdictModal({ item, open, onClose, onSuccess }: VerdictModalProps) {
  const [verdict, setVerdict] = React.useState<"confirmed" | "benign" | "insufficient_information">("confirmed")
  const [rationale, setRationale] = React.useState("")
  const [submitting, setSubmitting] = React.useState(false)

  React.useEffect(() => {
    if (item?.verdict) {
      const v = typeof item.verdict === "string" ? item.verdict : item.verdict.verdict
      if (v === "confirmed" || v === "benign" || v === "insufficient_information") {
        setVerdict(v)
      }
    } else {
      setVerdict("confirmed")
    }
    setRationale("")
  }, [item, open])

  if (!item) return null

  const inclusionProb = item.inclusion_prob ?? item.inclusion_probability ?? null

  const handleSubmit = async () => {
    setSubmitting(true)
    try {
      await api.recordVerdict({
        pack_id: item.pack_id || "",
        case_id: item.case_id,
        verdict,
        notes: rationale.trim(),
        rationale: rationale.trim(),
      })
      toast.success(`Verdict recorded for ${item.case_id}: ${verdict}`, {
        description: "Append-only audit ledger entry signed successfully.",
      })
      onSuccess()
      onClose()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to record verdict"
      toast.error("Verdict capture failed", { description: msg })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="max-w-md font-sans">
        <DialogHeader>
          <DialogTitle className="text-sm font-semibold">
            Record Supervisory Verdict
          </DialogTitle>
          <DialogDescription className="text-xs">
            Case: {item.case_id} · Entity: {item.entity_id}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* Inclusion Context */}
          <div className="border border-border p-3 rounded-md bg-muted/30 text-xs space-y-1">
            <div className="text-muted-foreground text-xs font-medium">Sampling Rationale:</div>
            <div className="text-foreground">{item.selected_because}</div>
            <div className="text-xs text-primary font-medium mt-1">
              Inclusion Probability (π): {inclusionProb !== null ? inclusionProb.toFixed(4) : "Deterministic Control"}
            </div>
          </div>

          {/* Verdict Selection */}
          <div className="space-y-2">
            <label className="text-xs font-medium text-muted-foreground">
              Examiner Determination
            </label>
            <div className="grid grid-cols-3 gap-2">
              <button
                type="button"
                onClick={() => setVerdict("confirmed")}
                className={`flex flex-col items-center justify-center p-3 rounded-md border text-xs transition-colors ${
                  verdict === "confirmed"
                    ? "border-rose-500 bg-rose-500/10 text-rose-600 dark:text-rose-400 font-bold"
                    : "border-border hover:bg-muted text-muted-foreground"
                }`}
              >
                <AlertTriangle className="h-4 w-4 mb-1" />
                <span>Confirmed</span>
              </button>

              <button
                type="button"
                onClick={() => setVerdict("benign")}
                className={`flex flex-col items-center justify-center p-3 rounded-md border text-xs transition-colors ${
                  verdict === "benign"
                    ? "border-emerald-500 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-bold"
                    : "border-border hover:bg-muted text-muted-foreground"
                }`}
              >
                <CheckCircle className="h-4 w-4 mb-1" />
                <span>Benign</span>
              </button>

              <button
                type="button"
                onClick={() => setVerdict("insufficient_information")}
                className={`flex flex-col items-center justify-center p-3 rounded-md border text-xs transition-colors ${
                  verdict === "insufficient_information"
                    ? "border-amber-500 bg-amber-500/10 text-amber-600 dark:text-amber-400 font-bold"
                    : "border-border hover:bg-muted text-muted-foreground"
                }`}
              >
                <HelpCircle className="h-4 w-4 mb-1" />
                <span>Inconclusive</span>
              </button>
            </div>
          </div>

          {/* Examiner Notes */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">
              Audit Justification / Examiner Notes
            </label>
            <textarea
              rows={3}
              placeholder="Record operational observations, verified artifacts, or reasons for closure..."
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
              className="w-full text-xs bg-card border border-border rounded-md p-2.5 focus:outline-none focus:border-primary placeholder:text-muted-foreground"
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" size="sm" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button variant="default" size="sm" onClick={handleSubmit} disabled={submitting}>
            {submitting ? "Signing to Ledger..." : "Commit Verdict"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
