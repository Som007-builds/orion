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
  ci_low?: number
  ci_high?: number
  ci_lower?: number
  ci_upper?: number
  standard_error?: number
  n_sampled?: number
  n_population?: number
  confidence_level?: number
  method?: string
  caveat?: string | null
}

export type VerdictType = 'confirmed' | 'benign' | 'insufficient_information'

export interface Verdict {
  verdict_id?: string
  pack_id: string
  case_id: string
  verdict: VerdictType
  notes?: string | null
  rationale?: string
  examiner_pseudo?: string
  examiner_actor?: string
  recorded_ts?: string
  recorded_at?: string
  ledger_entry_hash?: string
  evidence_seen?: string[]
  item_id?: string
}

export interface VerificationPrompt {
  prompt_type: string
  question: string
  expected_evidence?: string | null
}

export interface ReviewPackItemOut {
  case_id: string
  entity_id: string
  slice_type?: 'targeted' | 'control' | string
  severity?: string | null
  case_risk_score?: number
  risk_score?: number
  inclusion_prob?: number | null
  inclusion_probability?: number | null
  selected_because: string
  verification_prompts?: (VerificationPrompt | string)[]
  contributing_indicators?: string[]
  cluster_id?: string | null
  analyst_pseudo?: string | null
  finding_ids?: string[]
  verdict?: VerdictType | Verdict | null
  item_id?: string
  pack_id?: string
  stratum?: string
}

export interface ReviewPackOut {
  pack_id: string
  entity_id?: string
  created_ts?: string
  created_at?: string
  created_by?: string
  period_start?: string
  period_end?: string
  seed?: number
  n_target?: number
  n_control?: number
  n_selected?: number
  n_population?: number
  targeted_size?: number
  control_size?: number
  items_count?: number
  ht_estimate?: HTEstimate | null
  ht_prevalence?: HTEstimate | null
  items: ReviewPackItemOut[]
  status?: 'draft' | 'in_review' | 'closed' | string
  diversity_caps_applied?: Record<string, number>
  content_hash?: string | null
}

export interface ReviewPackListItem {
  pack_id: string
  created_ts: string
  created_by: string
  period_start: string
  period_end: string
  n_selected: number
  ht_estimate?: number | null
  content_hash?: string | null
}

export interface ReviewPackGenerateIn {
  entity_ids?: string[] | null
  n_target?: number
  n_control?: number
  period_start?: string | null
  period_end?: string | null
  seed?: number | null
  max_per_cluster?: number
  max_per_analyst?: number
  control_severity_strata?: string[]
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

// Trends & Change Points (Phase 2.15)
export type TrendMetric = 'egi' | 'nsi' | 'dts' | 'sap'

export interface TrendPointOut {
  period_start: string
  period_end: string
  value: number | null
  assessable?: boolean
  gap?: boolean
  run_id?: string | null
}

export interface TrendSeriesOut {
  entity_id: string
  metric: TrendMetric
  points: TrendPointOut[]
  n_scored: number
  n_gaps: number
}

export interface ChangePointOut {
  period_start: string
  period_end: string
  direction: 'up' | 'down'
  cohort_median_before: number
  cohort_median_after: number
  shift: number
  ss_reduction: number
  n_periods_before: number
  n_periods_after: number
}

export interface ChangePointsOut {
  metric: TrendMetric
  n_periods: number
  change_points: ChangePointOut[]
  note?: string | null
}

// Signed Pack Lifecycle (Phase 2.14)
export interface PackOut {
  pack_id: string
  version: string
  content_hash: string
  signature: string
  status: string
  staged_ts: string
  promoted_ts?: string | null
  signed_by?: string | null
}

export interface PackStageIn {
  source_path: string
  version: string
  pack_id?: string | null
  signed_by?: string | null
}

export interface PackShadowOut {
  pack_id: string
  status: string
  n_findings_changed: number
  n_findings_added: number
  n_findings_removed: number
  ledger_entry_hash: string
  diff_report?: Record<string, unknown>
  recommendation?: string | null
}

export interface PackTransitionOut {
  pack_id: string
  status: string
  ledger_entry_hash: string
  previous_id?: string | null
}

// Policy Profiles (Phase 2.7)
export interface PolicyProfileOut {
  profile_id: string
  version: string
  effective_from: string
  content_hash: string
  active: boolean
  summary?: Record<string, unknown>
  signature?: string | null
  previous_id?: string | null
}

export interface PolicyActivateIn {
  effective_from?: string | null
  notes?: string | null
}

export interface PolicyActivateOut {
  profile_id: string
  version: string
  activated: boolean
  ledger_entry_hash: string
  previous_id?: string | null
}

// Submissions, DQ, Mapping & Quarantine (Phase 2.5, 2.6)
export interface DQComponentOut {
  name: string
  score: number
  weight: number
  status: string
  details?: Record<string, unknown>
}

export interface QuarantineSummaryOut {
  stage: string
  count: number
  reasons: string[]
}

export interface DQReportOut {
  submission_id: string
  entity_id: string
  dq_score: number
  data_tier: string
  n_rows_loaded: number
  n_rows_quarantined: number
  interpretation: string
  components?: DQComponentOut[]
  quarantine_summary?: QuarantineSummaryOut[]
  warnings?: string[]
}

export interface QuarantineRowOut {
  quarantine_id: string
  failed_stage: string
  reason: string
  created_ts: string
  source_file?: string | null
  source_row?: number | null
  source_table?: string | null
  raw_payload?: string | null
}

export interface MappingSuggestionOut {
  source_column: string
  suggested_target: string
  confidence: number
  reason?: string
}

export interface MappingOut {
  submission_id: string
  approved: boolean
  approved_by?: string | null
  detected_profile?: string | null
  profile_version?: string | null
  vendor?: string | null
  timezone?: string | null
  column_map?: Record<string, string>
  severity_map?: Record<string, string>
  status_map?: Record<string, string>
  unmapped_columns?: string[]
  suggestions?: MappingSuggestionOut[]
  warnings?: string[]
}

export interface MappingApprovalIn {
  profile_id?: string | null
  column_map?: Record<string, string | Record<string, string>>
  severity_map?: Record<string, string>
  status_map?: Record<string, string>
  timezone?: string | null
  notes?: string | null
}

export interface MappingApprovalOut {
  submission_id: string
  profile_id: string
  approved: boolean
  ledger_entry_hash: string
  approved_at: string
  approved_by?: string | null
}

export interface SubmissionDetail extends SubmissionOut {
  manifest?: Record<string, unknown>
  dq_report?: DQReportOut
  mapping?: MappingOut
}

// Supervisory Brief Export (Phase 2.13)
export interface ReviewPackExportOut {
  pack_id: string
  content_hash: string
  ledger_head_hash: string
  formats?: string[]
  export_paths?: string[]
  signature?: string | null
  signed_by?: string | null
}

export interface ExportFormatInfo {
  format: string
  mime_type: string
  extension: string
  renders: string
}

// Indicator Catalogue & Scoring Runs (Phase 2.12)
export interface IndicatorCatalogueItem {
  indicator_id: string
  name: string
  description: string
  family: string
  primary_dimension: string
  secondary_dimensions?: string[]
  adverse_direction: 'low' | 'high' | string
  source: string
  status: string
  min_tier?: string | null
  required_fields?: string[]
  benign_explanations?: string[]
}

export interface RunOut {
  run_id: string
  created_ts: string
  status: string
  started_ts?: string | null
  finished_ts?: string | null
  n_findings?: number
  policy_hash?: string | null
  config_hash?: string | null
  code_version?: string | null
  pack_version?: string | null
  seed?: number | null
  error?: string | null
}

export interface RunTriggerIn {
  entity_ids?: string[] | null
  indicators?: string[] | null
  period_start?: string | null
  period_end?: string | null
  pack_id?: string | null
  policy_profile_id?: string | null
}

export interface BenchmarkOut {
  cohort: string
  metric: string
  n_peers: number
  peer_median: number
  peer_mad?: number | null
  method: string
  fell_back_to_covariate_model?: boolean
}

export interface UploadAccepted {
  job_id: string
  entity_id: string
  format: string
  status: string
  message: string
}

