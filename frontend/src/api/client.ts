import type {
  AnalysisOut,
  FindingOut,
  GitHubRepositoryOut,
  RepositoryOut,
  SyncStatusOut,
  UserOut,
  WorkflowOut,
  WorkflowRunOut,
} from "./types";

export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

// Full-page redirects (not fetch calls) - the GitHub OAuth/App-install dance
// requires the browser itself to navigate so it can carry/receive cookies.
export const GITHUB_LOGIN_URL = `${API_BASE_URL}/api/v1/auth/github/login`;
export const GITHUB_INSTALL_URL = `${API_BASE_URL}/api/v1/auth/github/install`;

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });

  if (response.status === 204) {
    return undefined as T;
  }

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      // response had no JSON body - fall back to statusText
    }
    throw new ApiError(detail, response.status);
  }

  return (await response.json()) as T;
}

export const api = {
  me: () => request<UserOut>("/api/v1/auth/me"),
  logout: () => request<void>("/api/v1/auth/logout", { method: "POST" }),

  githubRepositories: () => request<GitHubRepositoryOut[]>("/api/v1/github/repositories"),

  repositories: () => request<RepositoryOut[]>("/api/v1/repositories"),
  connectRepository: (githubRepoId: number) =>
    request<RepositoryOut>("/api/v1/repositories", {
      method: "POST",
      body: JSON.stringify({ github_repo_id: githubRepoId }),
    }),

  sync: (repositoryId: number) =>
    request<SyncStatusOut>(`/api/v1/repositories/${repositoryId}/sync`, { method: "POST" }),
  analyze: (repositoryId: number) =>
    request<AnalysisOut>(`/api/v1/repositories/${repositoryId}/analyze`, { method: "POST" }),

  workflows: (repositoryId: number) =>
    request<WorkflowOut[]>(`/api/v1/repositories/${repositoryId}/workflows`),
  runs: (repositoryId: number, params?: { workflowId?: number; limit?: number; offset?: number }) => {
    const query = new URLSearchParams();
    if (params?.workflowId != null) query.set("workflow_id", String(params.workflowId));
    if (params?.limit != null) query.set("limit", String(params.limit));
    if (params?.offset != null) query.set("offset", String(params.offset));
    const qs = query.toString();
    return request<WorkflowRunOut[]>(`/api/v1/repositories/${repositoryId}/runs${qs ? `?${qs}` : ""}`);
  },
  findings: (repositoryId: number, analysisId?: number) => {
    const qs = analysisId != null ? `?analysis_id=${analysisId}` : "";
    return request<FindingOut[]>(`/api/v1/repositories/${repositoryId}/findings${qs}`);
  },
};
