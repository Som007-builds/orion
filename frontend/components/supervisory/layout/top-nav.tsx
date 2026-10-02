"use client"

import * as React from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { useTheme } from "next-themes"
import {
  ShieldAlert,
  Lock,
  Sun,
  Moon,
  UserCheck,
  ChevronDown,
  Building2,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { ROLES, getActiveRole, setActiveRole } from "@/lib/rbac"
import { Role } from "@/lib/types"
import { api } from "@/lib/api"
import { EntityListItem } from "@/lib/types"

interface TopNavProps {
  activeEntityId?: string
  onEntityChange?: (entityId: string) => void
}

export function TopNav({ activeEntityId, onEntityChange }: TopNavProps) {
  const pathname = usePathname()
  const { theme, setTheme } = useTheme()
  const [mounted, setMounted] = React.useState(false)
  const [role, setRole] = React.useState<Role>("supervisor")
  const [entities, setEntities] = React.useState<EntityListItem[]>([])
  const [backendStatus, setBackendStatus] = React.useState<"online" | "offline" | "checking">("checking")
  const [isRoleMenuOpen, setIsRoleMenuOpen] = React.useState(false)
  const [isEntityMenuOpen, setIsEntityMenuOpen] = React.useState(false)

  React.useEffect(() => {
    setMounted(true)
    setRole(getActiveRole())

    // Check backend health
    api.getHealth()
      .then((h) => setBackendStatus(h.offline ? "offline" : "online"))
      .catch(() => setBackendStatus("offline"))

    // Fetch entity directory
    api.getEntities()
      .then((data) => {
        const safeData = Array.isArray(data) ? data : []
        setEntities(safeData)
        if (!activeEntityId && safeData.length > 0 && onEntityChange) {
          onEntityChange(safeData[0].id)
        }
      })
      .catch(() => {
        // Backend offline or empty
      })
  }, [activeEntityId, onEntityChange])

  const handleRoleChange = (newRole: Role) => {
    setActiveRole(newRole)
    setRole(newRole)
    setIsRoleMenuOpen(false)
    window.dispatchEvent(new Event("orion:role-changed"))
  }

  const roleDef = ROLES[role] || ROLES.supervisor

  const navItems = [
    { href: "/dashboard", label: "Executive Overview", visible: true },
    { href: "/review-packs", label: "Review Packs", visible: true },
    { href: "/submissions", label: "Submissions & DQ", visible: true },
    { href: "/governance/ledger", label: "Audit Ledger", visible: roleDef.canViewLedger },
    { href: "/reports", label: "Supervisory Brief", visible: roleDef.canExportBriefs },
  ]

  return (
    <header className="sticky top-0 z-40 w-full border-b border-border bg-card/95 backdrop-blur font-sans">
      {/* Top Enclave Control Bar */}
      <div className="flex h-10 items-center justify-between px-4 border-b border-border/60 text-xs">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 font-bold tracking-tight text-primary">
            <ShieldAlert className="h-4 w-4" />
            <span className="text-sm font-semibold">Orion SAT-SA</span>
          </div>
          <span className="text-border">|</span>
          <span className="text-muted-foreground text-xs hidden sm:inline">
            NCIIPC Supervisory Enclave
          </span>
          <div className="flex items-center gap-1.5 text-xs text-emerald-600 dark:text-emerald-400 font-medium">
            <Lock className="h-3 w-3" />
            <span>Air-Gapped Enclave</span>
          </div>
        </div>

        {/* Right tools: API status, entity switcher, role selector, theme */}
        <div className="flex items-center gap-2">
          {/* Backend Status indicator */}
          <div className="flex items-center gap-1.5 text-xs px-2.5 py-1 border border-border rounded-full bg-muted/40">
            <span
              className={`h-2 w-2 rounded-full ${
                backendStatus === "online"
                  ? "bg-emerald-500 animate-pulse"
                  : backendStatus === "checking"
                  ? "bg-amber-500"
                  : "bg-rose-500"
              }`}
            />
            <span className="text-muted-foreground capitalize">
              API: {backendStatus}
            </span>
          </div>

          {/* Entity Switcher dropdown */}
          <div className="relative">
            <button
              onClick={() => {
                setIsEntityMenuOpen(!isEntityMenuOpen)
                setIsRoleMenuOpen(false)
              }}
              className="flex items-center gap-1.5 h-7 px-2.5 text-xs border border-border rounded-md hover:bg-muted text-foreground bg-card"
            >
              <Building2 className="h-3.5 w-3.5 text-primary" />
              <span className="max-w-[130px] truncate font-medium">
                {activeEntityId || "Select Entity"}
              </span>
              <ChevronDown className="h-3 w-3 opacity-60" />
            </button>

            {isEntityMenuOpen && (
              <div className="absolute right-0 mt-1 w-64 border border-border bg-card shadow-lg rounded-md p-1.5 z-50">
                <div className="p-1.5 text-xs font-semibold border-b border-border text-muted-foreground">
                  Regulated CSE Enclaves ({entities.length})
                </div>
                <div className="max-h-56 overflow-y-auto py-1 space-y-0.5">
                  {entities.length === 0 ? (
                    <div className="p-2 text-xs text-muted-foreground text-center">
                      No entities reported from API.
                    </div>
                  ) : (
                    entities.map((e) => (
                      <button
                        key={e.id}
                        onClick={() => {
                          if (onEntityChange) onEntityChange(e.id)
                          setIsEntityMenuOpen(false)
                        }}
                        className={`w-full text-left p-1.5 text-xs hover:bg-muted rounded-md flex items-center justify-between transition-colors ${
                          e.id === activeEntityId ? "bg-primary/10 text-primary font-semibold" : ""
                        }`}
                      >
                        <span className="truncate">{e.name}</span>
                        <Badge variant="outline" className="text-[10px] ml-1">
                          {e.sap_tier}
                        </Badge>
                      </button>
                    ))
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Role Switcher */}
          <div className="relative">
            <button
              onClick={() => {
                setIsRoleMenuOpen(!isRoleMenuOpen)
                setIsEntityMenuOpen(false)
              }}
              className="flex items-center gap-1.5 h-7 px-2.5 text-xs border border-primary/30 bg-primary/10 text-primary rounded-md hover:bg-primary/20 font-medium"
            >
              <UserCheck className="h-3.5 w-3.5" />
              <span>{roleDef.label}</span>
              <ChevronDown className="h-3 w-3 opacity-60" />
            </button>

            {isRoleMenuOpen && (
              <div className="absolute right-0 mt-1 w-72 border border-border bg-card shadow-lg rounded-md p-1.5 z-50">
                <div className="p-1.5 text-xs font-semibold border-b border-border text-muted-foreground">
                  Switch Active Persona (RBAC)
                </div>
                <div className="space-y-1 py-1">
                  {(Object.keys(ROLES) as Role[]).map((rKey) => {
                    const r = ROLES[rKey]
                    return (
                      <button
                        key={rKey}
                        onClick={() => handleRoleChange(rKey)}
                        className={`w-full text-left p-2 hover:bg-muted rounded-md transition-colors ${
                          rKey === role ? "bg-primary/10 border-l-2 border-primary" : ""
                        }`}
                      >
                        <div className="font-semibold text-xs text-foreground">
                          {r.label}
                        </div>
                        <div className="text-[11px] text-muted-foreground leading-snug mt-0.5">
                          {r.description}
                        </div>
                      </button>
                    )
                  })}
                </div>
              </div>
            )}
          </div>

          {/* Theme toggle */}
          {mounted && (
            <button
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              className="h-7 w-7 flex items-center justify-center border border-border rounded-md hover:bg-muted text-muted-foreground hover:text-foreground"
              title="Toggle Theme"
            >
              {theme === "dark" ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
            </button>
          )}
        </div>
      </div>

      {/* Main Navigation Bar */}
      <div className="flex h-11 items-center px-4 overflow-x-auto gap-1">
        {navItems
          .filter((item) => item.visible)
          .map((item) => {
            const isActive = pathname === item.href || (item.href !== "/dashboard" && pathname.startsWith(item.href))
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center h-8 px-3.5 text-xs font-medium transition-colors rounded-md whitespace-nowrap ${
                  isActive
                    ? "bg-primary text-primary-foreground font-semibold shadow-sm"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted"
                }`}
              >
                {item.label}
              </Link>
            )
          })}
      </div>
    </header>
  )
}
