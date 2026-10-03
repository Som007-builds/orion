/**
 * Orion SAT-SA Core Domain Types
 * Strictly mirrors backend schemas (app/schemas/**) and docs/openapi-v1.json.
 */

export type Dimension = 'td' | 'inv' | 'esc' | 'ir' | 'so' | 'gov' | 'od' | 'cr'

export const DIMENSION_LABELS: Record<Dimension, string> = {
  td: 'Threat Detection',
  inv: 'Investigation',
  esc: 'Escalation',
  ir: 'Incident Response',
  so: 'Security Operations',
  gov: 'Governance and Oversight',
  od: 'Operational Discipline',
  cr: 'Cyber Resilience',
}

export type Assessability = 'assessable' | 'partial' | 'not_assessable'

export type SapTier = 'T1' | 'T2' | 'T3' | 'T4' | 'NOT_ASSESSABLE'

export type Role =
  | 'supervisor'
  | 'examiner'
  | 'auditor'
  | 'administrator'
  | 'data_custodian'

export interface RankInterval {
  rank: number
  low: number
  high: number
  method?: string
}

export interface DimensionScoreOut {
  dimension: Dimension
  score: number | null
  assessability: Assessability
  peer_median: number
  peer_mad: number
  effect_size: number
  n_indicators?: number
  is_gated?: boolean
}

export interface AssessabilitySummary {
  n_assessable: number
  n_partial: number
  n_not_assessable: number
  overall: Assessability
}

export interface EntityListItem {
  id: string
  entity_id?: string
  name: string
  sector: string
  soc_model: 'in-house' | 'MSSP' | 'hybrid'
  size_tier: 'small' | 'medium' | 'large'
  egi: number
  nsi: number
  dts: number
  sap_tier: SapTier
  sap_rank_interval: RankInterval
  assessability_summary?: AssessabilitySummary
  last_assessed?: string
}

export interface EntitySummaryOut {
  entity?: {
    entity_id: string
    name: string
    sector: string
    soc_model: 'in-house' | 'MSSP' | 'hybrid'
    size_tier: 'small' | 'medium' | 'large'
    coverage_type?: string
    critical_asset_count?: number
  }
  entity_id: string
  name: string
  sector: string
  soc_model: 'in-house' | 'MSSP' | 'hybrid'
  size_tier: 'small' | 'medium' | 'large'
  egi: number | null
  nsi: number | null
  dts: number | null
  sap?: number | null
  sap_tier: SapTier
  sap_rank_interval: RankInterval
  dimensions: DimensionScoreOut[]
  period_start?: string
  period_end?: string
  n_findings?: number
  n_not_assessable_dimensions?: number
  overall_assessability?: Assessability
  caveats?: string[]
}

export interface HTEstimate {
  estimate: number
  standard_error: number
  ci_lower: number
  ci_upper: number
  confidence_level: number
  method: string
}

export interface Verdict {
  verdict_id: string
  item_id: string
  pack_id: string
  case_id: string
  verdict: 'confirmed' | 'benign' | 'insufficient_information'
  rationale?: string
  examiner_actor: string
  recorded_at: string
}

export interface ReviewPackItemOut {
  item_id: string
  pack_id: string
  case_id: string
  entity_id: string
  risk_score: number
  inclusion_probability: number
  stratum: string
  selected_because: string
  verification_prompts: string[]
  verdict?: Verdict | null
}

export interface ReviewPackOut {
  pack_id: string
  entity_id: string
  created_at: string
  created_by: string
  seed: number
  targeted_size: number
  control_size: number
  items_count: number
  ht_prevalence?: HTEstimate | null
  items: ReviewPackItemOut[]
  status: 'draft' | 'in_review' | 'closed'
}

export interface ConfidenceBreakdown {
  n_term: number
  assessability_term: number
  data_trust_term: number
}

export interface Baseline {
  median: number
  mad: number
  percentile: number
  n_peers: number
}

export interface LineageInfo {
  submission_hashes: string[]
  pack_version: string
  policy_hash: string
  code_version: string
}

export interface FindingCardOut {
  finding_id: string
  indicator_id: string
  entity_id: string
  summary: string
  period_start: string
  period_end: string
  value: number | string | null
  value_units?: string
  effect_size: number
  confidence: number
  confidence_breakdown: ConfidenceBreakdown
  peer_baseline: Baseline
  self_baseline?: Baseline | null
  benign_explanations: string[]
  suggested_actions?: string[]
  is_low_confidence_lead: boolean
  assessability: Assessability
  primary_dimension: Dimension
  lineage: LineageInfo
}

export interface Counterfactual {
  parameter: string
  current_value: string | number
  threshold_to_clear: string | number
  guidance: string
}

export interface EvidenceOut {
  evidence_row_ids: string[]
  evidence_query: string
  sample_rows?: Record<string, unknown>[]
}

export interface SubmissionOut {
  submission_id: string
  entity_id: string
  period_start: string
  period_end: string
  received_ts: string
  dq_score: number
  version: number
  status: string
  quarantine_count?: number
}

export interface DimensionAssessability {
  dimension: Dimension
  assessability: Assessability
  missing_fields: string[]
  probed_fields: string[]
}

export interface AssessabilityReportOut {
  entity_id: string
  overall: Assessability
  interpretation: string
  n_assessable: number
  n_partial: number
  n_not_assessable: number
  dimensions: DimensionAssessability[]
}

export interface LedgerEntryOut {
  seq: number
  timestamp: string
  action: string
  actor: string
  role?: string
  entry_hash: string
  prev_hash: string
  payload_summary?: Record<string, unknown>
}

export interface LedgerVerifyOut {
  valid: boolean
  head_hash: string
  total_entries: number
  verified_at: string
}
