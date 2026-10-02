import { Role } from "./types"
export type { Role }

export interface RoleDefinition {
  id: Role
  label: string
  description: string
  canViewLedger: boolean
  canRecordVerdicts: boolean
  canExportBriefs: boolean
  canManagePacks: boolean
  canUploadSubmissions: boolean
  canApproveMappings: boolean
}

export const ROLES: Record<Role, RoleDefinition> = {
  supervisor: {
    id: "supervisor",
    label: "Supervisor",
    description: "NCIIPC supervisory authority; full audit scope, run approvals, signed brief export",
    canViewLedger: true,
    canRecordVerdicts: true,
    canExportBriefs: true,
    canManagePacks: true,
    canUploadSubmissions: true,
    canApproveMappings: true,
  },
  examiner: {
    id: "examiner",
    label: "Field Examiner",
    description: "Audit examiner; review packs triage, verdict capture, evidence inspection",
    canViewLedger: false,
    canRecordVerdicts: true,
    canExportBriefs: false,
    canManagePacks: false,
    canUploadSubmissions: false,
    canApproveMappings: false,
  },
  auditor: {
    id: "auditor",
    label: "External Auditor",
    description: "Independent oversight; read-only verification of hash chains and provenance",
    canViewLedger: true,
    canRecordVerdicts: false,
    canExportBriefs: true,
    canManagePacks: false,
    canUploadSubmissions: false,
    canApproveMappings: false,
  },
  administrator: {
    id: "administrator",
    label: "System Administrator",
    description: "Infrastructure management; pack promotions, shadow runs, policy profiles",
    canViewLedger: true,
    canRecordVerdicts: false,
    canExportBriefs: false,
    canManagePacks: true,
    canUploadSubmissions: true,
    canApproveMappings: true,
  },
  data_custodian: {
    id: "data_custodian",
    label: "Data Custodian",
    description: "CSE intake officer; batch uploads, ingestion telemetry, vendor mapping reviews",
    canViewLedger: false,
    canRecordVerdicts: false,
    canExportBriefs: false,
    canManagePacks: false,
    canUploadSubmissions: true,
    canApproveMappings: true,
  },
}

const STORAGE_KEY_ROLE = "orion_actor_role"
const STORAGE_KEY_ACTOR = "orion_actor_name"

export function getActiveRole(): Role {
  if (typeof window === "undefined") return "supervisor"
  const saved = localStorage.getItem(STORAGE_KEY_ROLE) as Role
  return saved && ROLES[saved] ? saved : "supervisor"
}

export function setActiveRole(role: Role): void {
  if (typeof window === "undefined") return
  localStorage.setItem(STORAGE_KEY_ROLE, role)
}

export function getActiveActor(): string {
  if (typeof window === "undefined") return "examiner.singh"
  const saved = localStorage.getItem(STORAGE_KEY_ACTOR)
  return saved || "examiner.singh"
}

export function setActiveActor(actor: string): void {
  if (typeof window === "undefined") return
  localStorage.setItem(STORAGE_KEY_ACTOR, actor)
}
