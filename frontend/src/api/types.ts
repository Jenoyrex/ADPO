// Mirrors backend/app/schemas/*.py exactly - keep in sync with those files.

export interface UserOut {
  id: number;
  github_login: string;
  avatar_url: string | null;
  email: string | null;
}

export interface GitHubRepositoryOut {
  github_repo_id: number;
  owner_login: string;
  name: string;
  full_name: string;
  private: boolean;
  default_branch: string;
  html_url: string;
  installation_id: number;
  already_connected: boolean;
}

export interface RepositoryOut {
  id: number;
  github_repo_id: number;
  owner_login: string;
  name: string;
  full_name: string;
  default_branch: string;
  private: boolean;
  html_url: string;
  last_synced_at: string | null;
}

export interface SyncStatusOut {
  repository_id: number;
  status: string;
  workflows_synced: number;
  runs_synced: number;
  jobs_synced: number;
  steps_synced: number;
  started_at: string;
  completed_at: string;
}

export interface WorkflowOut {
  id: number;
  github_workflow_id: number;
  name: string;
  path: string;
  state: string;
}

export interface StepOut {
  number: number;
  name: string;
  status: string;
  conclusion: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface JobOut {
  github_job_id: number;
  name: string;
  status: string;
  conclusion: string | null;
  started_at: string | null;
  completed_at: string | null;
  runs_on: string | null;
  steps: StepOut[];
}

export interface WorkflowRunOut {
  id: number;
  github_run_id: number;
  run_number: number;
  run_attempt: number;
  branch: string;
  event: string;
  status: string;
  conclusion: string | null;
  run_created_at: string;
  run_updated_at: string | null;
  workflow_id: number | null;
}

export type Severity = "low" | "medium" | "high" | "critical";
export type Confidence = "low" | "medium" | "high";

export interface EstimatedSavingsOut {
  low_seconds: number | null;
  high_seconds: number | null;
  basis: string | null;
  unknown: boolean;
}

export interface FindingOut {
  id: number;
  analyzer_type: string;
  title: string;
  description: string;
  evidence: string[];
  metrics: Record<string, unknown>;
  severity: Severity;
  confidence: Confidence;
  recommendation: string;
  estimated_savings_range: EstimatedSavingsOut | null;
}

export interface AnalysisOut {
  id: number;
  repository_id: number;
  status: string;
  runs_analyzed_count: number;
  findings: FindingOut[];
}
