"use client"

import * as React from "react"
import { TopNav } from "@/components/supervisory/layout/top-nav"
import { EntityProvider, useEntity } from "@/lib/entity-context"
import { Toaster } from "@/components/ui/sonner"

function DashboardShell({ children }: { children: React.ReactNode }) {
  const { activeEntityId, setActiveEntityId } = useEntity()

  return (
    <div className="min-h-screen flex flex-col bg-background text-foreground antialiased font-sans">
      <TopNav
        activeEntityId={activeEntityId}
        onEntityChange={setActiveEntityId}
      />
      <main className="flex-1 w-full max-w-[1600px] mx-auto p-4 sm:p-6 space-y-6">
        {children}
      </main>
      <footer className="border-t border-border py-3 px-6 text-center text-[11px] font-mono text-muted-foreground flex flex-col sm:flex-row items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-foreground uppercase">Orion SAT-SA</span>
          <span>·</span>
          <span>NCIIPC Cyber Supervisory Framework v2</span>
        </div>
        <div>Strict Air-Gap Enclave · Hypotheses, Not Verdicts · Signed Provenance</div>
      </footer>
      <Toaster position="bottom-right" />
    </div>
  )
}

export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <EntityProvider>
      <DashboardShell>{children}</DashboardShell>
    </EntityProvider>
  )
}
