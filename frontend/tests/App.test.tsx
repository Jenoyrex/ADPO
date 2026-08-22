import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { ApiError } from "../src/api/client";

vi.mock("../src/api/client", async () => {
  const actual = await vi.importActual<typeof import("../src/api/client")>("../src/api/client");
  return {
    ...actual,
    api: {
      me: vi.fn(),
      logout: vi.fn(),
      repositories: vi.fn().mockResolvedValue([]),
      githubRepositories: vi.fn().mockResolvedValue([]),
      connectRepository: vi.fn(),
      sync: vi.fn(),
      analyze: vi.fn(),
      workflows: vi.fn().mockResolvedValue([]),
      runs: vi.fn().mockResolvedValue([]),
      findings: vi.fn().mockResolvedValue([]),
    },
  };
});

import { api } from "../src/api/client";

function renderApp(initialPath: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialPath]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("App auth gate", () => {
  beforeEach(() => {
    vi.mocked(api.me).mockReset();
  });

  it("redirects to the login page when the session is unauthenticated (401)", async () => {
    vi.mocked(api.me).mockRejectedValue(new ApiError("unauthorized", 401));
    renderApp("/dashboard");

    await waitFor(() => expect(screen.getByText("Sign in with GitHub")).toBeInTheDocument());
  });

  it("renders the dashboard for an authenticated user", async () => {
    vi.mocked(api.me).mockResolvedValue({
      id: 1,
      github_login: "octocat",
      avatar_url: null,
      email: null,
    });
    renderApp("/dashboard");

    await waitFor(() => expect(screen.getByText("octocat")).toBeInTheDocument());
    expect(screen.getByText("Your repositories")).toBeInTheDocument();
  });
});
