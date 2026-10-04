"use client"

import * as React from "react"
import { PackAdmin } from "@/components/supervisory/governance/pack-admin"
import { getActiveRole, ROLES } from "@/lib/rbac"
import { Role } from "@/lib/types"

export default function PacksGovernancePage() {
  const [role, setRole] = React.useState<Role>("supervisor")

  React.useEffect(() => {
    setRole(getActiveRole())
    const handleRoleChanged = () => {
      setRole(getActiveRole())
    }
    window.addEventListener("orion:role-changed", handleRoleChanged)
    return () => window.removeEventListener("orion:role-changed", handleRoleChanged)
  }, [])

  const roleDef = ROLES[role] || ROLES.supervisor
  const canManage = roleDef.canManagePacks

  return (
    <div className="space-y-6 font-sans">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between pb-2 border-b border-border/60 gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-foreground">
            Signed Rule Pack Lifecycle Governance
          </h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Phase 2.14: Enclave pack management — stage, shadow run diff, promote to active, and rollback
          </p>
        </div>
      </div>

      <PackAdmin canManage={canManage} />
    </div>
  )
}
