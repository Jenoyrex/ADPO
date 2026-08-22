import { ApiError } from "../api/client";

export interface FriendlyError {
  message: string;
  isSessionExpired: boolean;
  detail: string | null;
}

// Translates a raw thrown error into a message a developer can act on
// without decoding an HTTP status code, while keeping the original detail
// available (callers can surface it behind a "technical details" toggle).
export function friendlyError(error: unknown, context: string): FriendlyError {
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return {
        message: "Your GitHub session has expired.",
        isSessionExpired: true,
        detail: error.message,
      };
    }
    if (error.status === 404) {
      return {
        message: `ADPO couldn't find this ${context}.`,
        isSessionExpired: false,
        detail: error.message,
      };
    }
    if (error.status >= 500 || error.status === 502) {
      return {
        message: `ADPO couldn't reach GitHub while loading this ${context}.`,
        isSessionExpired: false,
        detail: error.message,
      };
    }
    return {
      message: `ADPO couldn't load this ${context}.`,
      isSessionExpired: false,
      detail: error.message,
    };
  }
  return {
    message: `ADPO couldn't load this ${context}.`,
    isSessionExpired: false,
    detail: error instanceof Error ? error.message : String(error),
  };
}
