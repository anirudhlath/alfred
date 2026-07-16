export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (resp.status === 401) {
    if (location.pathname !== "/login") location.assign("/login");
    throw new ApiError(401, "Authentication required");
  }
  if (!resp.ok) {
    const text = await resp.text();
    let message = text;
    try {
      const parsed: unknown = JSON.parse(text);
      if (
        parsed &&
        typeof parsed === "object" &&
        "detail" in parsed &&
        typeof (parsed as { detail: unknown }).detail === "string"
      ) {
        message = (parsed as { detail: string }).detail;
      }
    } catch {
      // keep raw text
    }
    throw new ApiError(resp.status, message);
  }
  return (await resp.json()) as T;
}

export const post = <T>(path: string, body?: unknown): Promise<T> =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const del = <T>(path: string): Promise<T> => api<T>(path, { method: "DELETE" });
