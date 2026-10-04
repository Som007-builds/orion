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
  SubmissionDetail,
  LedgerEntryOut,
  LedgerVerifyOut,
  TrendMetric,
  TrendSeriesOut,
  ChangePointsOut,
  PackOut,
  PackStageIn,
  PackShadowOut,
  PackTransitionOut,
  PolicyProfileOut,
  PolicyActivateIn,
  PolicyActivateOut,
  DQReportOut,
  MappingOut,
  MappingApprovalIn,
  MappingApprovalOut,
  QuarantineRowOut,
  ReviewPackExportOut,
  ExportFormatInfo,
  IndicatorCatalogueItem,
  RunOut,
  RunTriggerIn,
  BenchmarkOut,
  UploadAccepted,
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
    let errorMessage = `API Error (${res.status}): ${res.statusText}`
    try {
      const errJson = await res.json()
      if (errJson) {
        if (typeof errJson.message === "string" && errJson.message) {
          errorMessage = errJson.message
        } else if (typeof errJson.detail === "string" && errJson.detail) {
          errorMessage = errJson.detail
        } else if (Array.isArray(errJson.detail)) {
          errorMessage = errJson.detail.map((d: any) => d.msg || JSON.stringify(d)).join(", ")
        }
        errorDetail = typeof errJson.detail === "string" ? errJson.detail : (errJson.message || JSON.stringify(errJson))
      }
    } catch {
      try {
        errorDetail = await res.text()
        if (errorDetail) errorMessage = errorDetail
      } catch {
        // ignore fallback text error
      }
    }

    if (res.status === 503) {
      throw new ApiError(503, errorMessage || "Service Pending Integration", errorDetail)
    }
    if (res.status === 401 || res.status === 403) {
      throw new ApiError(res.status, errorMessage || "Access Denied by RBAC Enclave Policy", errorDetail)
    }

    throw new ApiError(res.status, errorMessage, errorDetail)
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
    const res = await request<PagedResponse<any> | any[]>("/review-packs")
    const list = Array.isArray(res) ? res : (Array.isArray(res?.items) ? res.items : [])
    return list.map((item: any) => ({
      ...item,
      items_count: item.n_selected ?? item.items_count ?? (Array.isArray(item.items) ? item.items.length : 0),
      items: item.items || [],
    }))
  },

  async getReviewPack(id: string): Promise<ReviewPackOut> {
    return request<ReviewPackOut>(`/review-packs/${encodeURIComponent(id)}`)
  },

  async createReviewPack(data: {
    entity_id?: string
    entity_ids?: string[]
    n_target?: number
    targeted_size?: number
    n_control?: number
    control_size?: number
    seed?: number
    period_start?: string
    period_end?: string
    max_per_cluster?: number
    max_per_analyst?: number
    control_severity_strata?: string[]
  }): Promise<ReviewPackOut> {
    const payload: Record<string, unknown> = {}

    if (data.entity_ids && data.entity_ids.length > 0) {
      const filtered = data.entity_ids.filter((id) => id && id !== "all")
      if (filtered.length > 0) {
        payload.entity_ids = filtered
      }
    } else if (data.entity_id && data.entity_id !== "all") {
      payload.entity_ids = [data.entity_id]
    }

    payload.n_target = data.n_target ?? data.targeted_size ?? 20
    payload.n_control = data.n_control ?? data.control_size ?? 10

    if (data.seed !== undefined && data.seed !== null) {
      payload.seed = data.seed
    }
    if (data.period_start) payload.period_start = data.period_start
    if (data.period_end) payload.period_end = data.period_end
    if (data.max_per_cluster) payload.max_per_cluster = data.max_per_cluster
    if (data.max_per_analyst) payload.max_per_analyst = data.max_per_analyst
    if (data.control_severity_strata) payload.control_severity_strata = data.control_severity_strata

    return request<ReviewPackOut>("/review-packs", {
      method: "POST",
      body: JSON.stringify(payload),
    })
  },

  async recordVerdict(data: {
    pack_id: string
    case_id: string
    verdict: "confirmed" | "benign" | "insufficient_information"
    notes?: string
    rationale?: string
    evidence_seen?: string[]
    item_id?: string
  }): Promise<Verdict> {
    const payload: Record<string, unknown> = {
      pack_id: data.pack_id,
      case_id: data.case_id,
      verdict: data.verdict,
      notes: data.notes || data.rationale || null,
      evidence_seen: data.evidence_seen || [],
    }
    return request<Verdict>("/verdicts", {
      method: "POST",
      body: JSON.stringify(payload),
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

  // Trends & Change Points (Phase 2.15)
  async getTrends(
    entityId: string,
    metric: TrendMetric = "sap",
    periodStart?: string,
    periodEnd?: string
  ): Promise<TrendSeriesOut> {
    const params = new URLSearchParams({ entity_id: entityId, metric })
    if (periodStart) params.set("period_start", periodStart)
    if (periodEnd) params.set("period_end", periodEnd)
    return request<TrendSeriesOut>(`/trends?${params.toString()}`)
  },

  async getChangePoints(
    metric: TrendMetric = "sap",
    periodStart?: string,
    periodEnd?: string
  ): Promise<ChangePointsOut> {
    const params = new URLSearchParams({ metric })
    if (periodStart) params.set("period_start", periodStart)
    if (periodEnd) params.set("period_end", periodEnd)
    return request<ChangePointsOut>(`/trends/change-points?${params.toString()}`)
  },

  // Signed Pack Lifecycle (Phase 2.14)
  async stagePack(data: PackStageIn): Promise<PackOut> {
    return request<PackOut>("/packs/stage", {
      method: "POST",
      body: JSON.stringify(data),
    })
  },

  async shadowPack(packId: string): Promise<PackShadowOut> {
    return request<PackShadowOut>(`/packs/${encodeURIComponent(packId)}/shadow`, {
      method: "POST",
    })
  },

  async promotePack(packId: string): Promise<PackTransitionOut> {
    return request<PackTransitionOut>(`/packs/${encodeURIComponent(packId)}/promote`, {
      method: "POST",
    })
  },

  async rollbackPack(packId: string): Promise<PackTransitionOut> {
    return request<PackTransitionOut>(`/packs/${encodeURIComponent(packId)}/rollback`, {
      method: "POST",
    })
  },

  // Policy Profiles (Phase 2.7)
  async getPolicyProfiles(): Promise<PolicyProfileOut[]> {
    const res = await request<PolicyProfileOut[] | { items: PolicyProfileOut[] }>("/policy-profiles")
    if (Array.isArray(res)) return res
    return Array.isArray((res as any)?.items) ? (res as any).items : []
  },

  async getActivePolicyProfile(): Promise<PolicyProfileOut> {
    return request<PolicyProfileOut>("/policy-profiles/active")
  },

  async activatePolicyProfile(profileId: string, data: PolicyActivateIn = {}): Promise<PolicyActivateOut> {
    return request<PolicyActivateOut>(`/policy-profiles/${encodeURIComponent(profileId)}/activate`, {
      method: "POST",
      body: JSON.stringify(data),
    })
  },

  // Supervisory Brief Export (Phase 2.13)
  async exportReviewPack(packId: string, fmt: "pdf" | "md" | "json" = "pdf"): Promise<ReviewPackExportOut> {
    return request<ReviewPackExportOut>(`/review-packs/${encodeURIComponent(packId)}/export?fmt=${fmt}`)
  },

  async getExportFormats(): Promise<ExportFormatInfo[]> {
    return request<ExportFormatInfo[]>("/exports/formats")
  },

  // Submissions Deep Details: DQ, Mapping & Quarantine (Phase 2.5, 2.6)
  async getSubmissionDQ(submissionId: string): Promise<DQReportOut> {
    return request<DQReportOut>(`/submissions/${encodeURIComponent(submissionId)}/dq`)
  },

  async getSubmissionMapping(submissionId: string): Promise<MappingOut> {
    return request<MappingOut>(`/submissions/${encodeURIComponent(submissionId)}/mapping`)
  },

  async approveSubmissionMapping(submissionId: string, data: MappingApprovalIn): Promise<MappingApprovalOut> {
    return request<MappingApprovalOut>(`/submissions/${encodeURIComponent(submissionId)}/mapping/approve`, {
      method: "POST",
      body: JSON.stringify(data),
    })
  },

  async getSubmissionQuarantine(
    submissionId: string,
    limit = 50,
    offset = 0
  ): Promise<PagedResponse<QuarantineRowOut>> {
    const res = await request<PagedResponse<QuarantineRowOut> | QuarantineRowOut[]>(
      `/submissions/${encodeURIComponent(submissionId)}/quarantine?limit=${limit}&offset=${offset}`
    )
    if (Array.isArray(res)) {
      return {
        items: res,
        total: res.length,
        limit,
        offset,
        has_more: false,
      }
    }
    return res
  },

  async seedIngest(seedId: string): Promise<UploadAccepted> {
    return request<UploadAccepted>(`/ingest/seed/${encodeURIComponent(seedId)}`, {
      method: "POST",
    })
  },

  // Indicators & Runs (Phase 2.12)
  async getIndicators(): Promise<IndicatorCatalogueItem[]> {
    const res = await request<IndicatorCatalogueItem[] | { items: IndicatorCatalogueItem[] }>("/indicators")
    if (Array.isArray(res)) return res
    return Array.isArray((res as any)?.items) ? (res as any).items : []
  },

  async getRuns(limit = 50, offset = 0): Promise<RunOut[]> {
    const res = await request<PagedResponse<RunOut> | RunOut[]>(`/runs?limit=${limit}&offset=${offset}`)
    if (Array.isArray(res)) return res
    return Array.isArray((res as any)?.items) ? (res as any).items : []
  },

  async triggerRun(data: RunTriggerIn = {}): Promise<RunOut> {
    return request<RunOut>("/runs", {
      method: "POST",
      body: JSON.stringify(data),
    })
  },

  async getBenchmarks(): Promise<BenchmarkOut[]> {
    const res = await request<BenchmarkOut[] | { items: BenchmarkOut[] }>("/benchmarks")
    if (Array.isArray(res)) return res
    return Array.isArray((res as any)?.items) ? (res as any).items : []
  },
}

