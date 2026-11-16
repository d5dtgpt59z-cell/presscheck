/** Requests stay on this origin: desktop authorization must never reach another host. */
const TOKEN_KEY = "presscheck.desktop.token.v1";
let memoryToken = "";

export function captureDesktopToken(): boolean {
  if (typeof window === "undefined") return false;
  const url = new URL(window.location.href);
  const token = url.searchParams.get("token");
  if (token) {
    if (!/^[A-Za-z0-9_-]{20,256}$/.test(token)) throw new Error("The local workspace link has an invalid access token. Reopen PressCheck Studio.");
    memoryToken = token;
    try { window.sessionStorage.setItem(TOKEN_KEY, token); } catch { /* The in-memory token still works in this tab. */ }
    url.searchParams.delete("token");
    window.history.replaceState(null, "", url.pathname + url.search + url.hash);
  }
  if (!memoryToken) {
    try { memoryToken = window.sessionStorage.getItem(TOKEN_KEY) || ""; } catch { /* Storage may be unavailable. */ }
  }
  return Boolean(memoryToken);
}

export async function productionFetch(path: string, init: RequestInit = {}) {
  if (!path.startsWith("/api/") || path.includes("\\") || path.startsWith("//")) throw new Error("Invalid local API address.");
  captureDesktopToken();
  const headers = new Headers(init.headers);
  if (memoryToken) headers.set("Authorization", `Bearer ${memoryToken}`);
  const response = await fetch(path, { ...init, headers, redirect: "error", credentials: "same-origin" });
  if (!response.ok) {
    let detail = `Request failed (${response.status}).`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((item: any) => item.msg).filter(Boolean).join("; ");
    } catch { /* Use the status when the server has no JSON error. */ }
    if (response.status === 401) detail = "Local workspace access expired. Reopen PressCheck Studio to reconnect.";
    throw new Error(detail);
  }
  return response;
}

export async function productionJSON<T>(path: string, init: RequestInit = {}): Promise<T> {
  return (await productionFetch(path, init)).json();
}

export function downloadBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function downloadAPI(path: string, name: string) {
  downloadBlob(await (await productionFetch(path)).blob(), name);
}
