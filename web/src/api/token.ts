const STORAGE_KEY = "bianca.api_token";

export function getApiToken(): string {
  return localStorage.getItem(STORAGE_KEY)?.trim() ?? "";
}

export function setApiToken(token: string): void {
  const trimmed = token.trim();
  if (trimmed) {
    localStorage.setItem(STORAGE_KEY, trimmed);
  } else {
    localStorage.removeItem(STORAGE_KEY);
  }
}

export function authHeaders(): Record<string, string> {
  const token = getApiToken();
  if (!token) return {};
  return { Authorization: `Bearer ${token}` };
}
