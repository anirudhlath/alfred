export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (resp.status === 401) {
    if (location.pathname !== "/login") location.assign("/login");
    throw new ApiError(401, "Authentication required");
  }
  if (!resp.ok) throw new ApiError(resp.status, await resp.text());
  return (await resp.json()) as T;
}

export const post = <T>(path: string, body?: unknown): Promise<T> =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const del = <T>(path: string): Promise<T> => api<T>(path, { method: "DELETE" });
