import { getActiveActor, getActiveRole, BACKEND_ROLE_MAP } from "./rbac"
import {
  EntityListItem,
  EntitySummaryOut,
  AssessabilityReportOut,
  ReviewPackOut,
  Verdict,
  FindingCardOut,
  Counterfactual,
  EvidenceOut,
  SubmissionOut,
  LedgerEntryOut,
  LedgerVerifyOut,
} from "./types"

const BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000/api/v1"

export class ApiError extends Error {
  status: number
  detail?: string

  constructor(status: number, message: string, detail?: string) {
    super(message)
    this.status = status
    this.detail = detail
    this.name = "ApiError"
  }
}

export interface PagedResponse<T> {
  items: T[]
  total: number
  limit: number
  offset: number
  has_more: boolean
}

async function request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const role = getActiveRole()
  const actor = getActiveActor()

  const url = `${BASE_URL}${endpoint}`
  const headers = new Headers(options.headers || {})
  headers.set("Accept", "application/json")
  headers.set("X-Role", BACKEND_ROLE_MAP[role] || "Supervisor")
  headers.set("X-Actor", actor)

  if (options.body && typeof options.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json")
  }

  let res: Response
  try {
    res = await fetch(url, {
      ...options,
      headers,
    })
  } catch (err: unknown) {
    const errorMsg = err instanceof Error ? err.message : "Connection failed"
    throw new ApiError(
      0,
      `Backend unreachable at ${BASE_URL}. Ensure FastAPI is running on port 8000.`,
      errorMsg
    )
  }

  if (!res.ok) {
    let errorDetail = ""
    try {
      const errJson = await res.json()
      errorDetail = errJson.detail || JSON.stringify(errJson)
    } catch {
      errorDetail = await res.text()
    }

    if (res.status === 503) {
      throw new ApiError(503, "Service Pending Integration", errorDetail)
    }
    if (res.status === 401 || res.status === 403) {
      throw new ApiError(res.status, "Access Denied by RBAC Enclave Policy", errorDetail)
    }

    throw new ApiError(res.status, `API Error (${res.status}): ${res.statusText}`, errorDetail)
  }

  return res.json()
}

export const api = {
  async getHealth(): Promise<{ status: string; offline?: boolean; version?: string }> {
    const healthUrl = BASE_URL.replace("/api/v1", "/health")
    try {
      const res = await fetch(healthUrl)
      if (res.ok) return await res.json()
      return { status: "offline", offline: false }
    } catch {
      return { status: "offline", offline: false }
    }
  },

  async getEntities(): Promise<EntityListItem[]> {
    const res = await request<PagedResponse<any> | any[]>("/entities")
    const list = Array.isArray(res) ? res : (Array.isArray(res?.items) ? res.items : [])
    return list.map((item: any) => ({
      ...item,
      id: item.entity_id || item.id,
      entity_id: item.entity_id || item.id,
    }))
  },

  async getEntitySummary(id: string): Promise<EntitySummaryOut> {
    const raw = await request<any>(`/entities/${encodeURIComponent(id)}/summary`)
    return {
      ...raw,
      entity_id: raw.entity?.entity_id || raw.entity_id || id,
      name: raw.entity?.name || raw.name || id,
      sector: raw.entity?.sector || raw.sector || "",
      soc_model: raw.entity?.soc_model || raw.soc_model || "in-house",
      size_tier: raw.entity?.size_tier || raw.size_tier || "medium",
    }
  },

  async getEntityAssessability(id: string): Promise<AssessabilityReportOut> {
    return request<AssessabilityReportOut>(`/entities/${encodeURIComponent(id)}/assessability`)
  },

  async getReviewPacks(): Promise<ReviewPackOut[]> {
    const res = await request<PagedResponse<ReviewPackOut> | ReviewPackOut[]>("/review-packs")
    if (Array.isArray(res)) return res
    return Array.isArray(res?.items) ? res.items : []
  },

  async getReviewPack(id: string): Promise<ReviewPackOut> {
    return request<ReviewPackOut>(`/review-packs/${encodeURIComponent(id)}`)
  },

  async createReviewPack(data: {
    entity_id: string
    targeted_size: number
    control_size: number
    seed?: number
  }): Promise<ReviewPackOut> {
    return request<ReviewPackOut>("/review-packs", {
      method: "POST",
      body: JSON.stringify(data),
    })
  },

  async recordVerdict(data: {
    item_id: string
    pack_id: string
    case_id: string
    verdict: "confirmed" | "benign" | "insufficient_information"
    rationale?: string
  }): Promise<Verdict> {
    return request<Verdict>("/verdicts", {
      method: "POST",
      body: JSON.stringify(data),
    })
  },

  async getFindings(entityId?: string): Promise<FindingCardOut[]> {
    const query = entityId ? `?entity_id=${encodeURIComponent(entityId)}` : ""
    const res = await request<PagedResponse<FindingCardOut> | FindingCardOut[]>(`/findings${query}`)
    if (Array.isArray(res)) return res
    return Array.isArray(res?.items) ? res.items : []
  },

  async getFinding(id: string): Promise<FindingCardOut> {
    return request<FindingCardOut>(`/findings/${encodeURIComponent(id)}`)
  },

  async getFindingEvidence(id: string): Promise<EvidenceOut> {
    return request<EvidenceOut>(`/findings/${encodeURIComponent(id)}/evidence`)
  },

  async getFindingCounterfactual(id: string): Promise<Counterfactual[]> {
    const res = await request<Counterfactual[] | { items: Counterfactual[] }>(`/findings/${encodeURIComponent(id)}/counterfactual`)
    if (Array.isArray(res)) return res
    return Array.isArray(res?.items) ? res.items : []
  },

  async getSubmissions(entityId?: string): Promise<SubmissionOut[]> {
    const query = entityId ? `?entity_id=${encodeURIComponent(entityId)}` : ""
    const res = await request<PagedResponse<SubmissionOut> | SubmissionOut[]>(`/submissions${query}`)
    if (Array.isArray(res)) return res
    return Array.isArray(res?.items) ? res.items : []
  },

  async getSubmission(id: string): Promise<SubmissionOut> {
    return request<SubmissionOut>(`/submissions/${encodeURIComponent(id)}`)
  },

  async getLedger(limit = 50, offset = 0): Promise<LedgerEntryOut[]> {
    const res = await request<PagedResponse<LedgerEntryOut> | LedgerEntryOut[]>(`/ledger?limit=${limit}&offset=${offset}`)
    if (Array.isArray(res)) return res
    return Array.isArray(res?.items) ? res.items : []
  },

  async verifyLedger(): Promise<LedgerVerifyOut> {
    return request<LedgerVerifyOut>("/ledger/verify")
  },
}
