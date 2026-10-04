function resolveBaseUrl() {
  const configured = (process.env.REACT_APP_BACKEND_URL || "http://localhost:8001").replace(/\/$/, "");
  if (typeof window === "undefined") return configured;
  try {
    const configuredUrl = new URL(configured);
    const pageHost = window.location.hostname;
    const configuredIsLoopback =
      configuredUrl.hostname === "localhost" || configuredUrl.hostname === "127.0.0.1";
    const pageIsLoopback = pageHost === "localhost" || pageHost === "127.0.0.1";
    if (configuredIsLoopback && pageHost && !pageIsLoopback) {
      configuredUrl.hostname = pageHost;
      return configuredUrl.origin;
    }
    if (pageIsLoopback && !configuredIsLoopback) {
      configuredUrl.hostname = pageHost === "127.0.0.1" ? "127.0.0.1" : "localhost";
      return configuredUrl.origin;
    }
  } catch {
    /* keep configured */
  }
  return configured;
}

const BASE_URL = resolveBaseUrl();

// A 401 from anything but the sign-in calls means the session behind this tab is
// gone (signed out in another tab, expired, cookie cleared) while the page still
// shows the signed-in app. AuthContext listens for this and sends the tab back to
// sign-in; before, every page just came back empty and saved jobs looked deleted
// (2026-09-26).
export const SESSION_ENDED_EVENT = "cinemind:session-ended";

export class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

async function request(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body !== undefined && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const { params, ...fetchOptions } = options;
  let url = `${resolveBaseUrl()}/api${path}`;
  if (params && typeof params === "object") {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") {
        query.set(key, String(value));
      }
    });
    const qs = query.toString();
    if (qs) url += `?${qs}`;
  }

  const response = await fetch(url, {
    ...fetchOptions,
    credentials: "include",
    headers,
    body:
      fetchOptions.body !== undefined &&
      !(fetchOptions.body instanceof FormData) &&
      typeof fetchOptions.body !== "string"
        ? JSON.stringify(fetchOptions.body)
        : fetchOptions.body,
  });

  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/auth/") && typeof window !== "undefined") {
      window.dispatchEvent(new Event(SESSION_ENDED_EVENT));
    }
    const detail = data?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((item) => item.msg).join(". ")
          : `Request failed (${response.status})`;
    throw new ApiError(message, response.status, data);
  }
  return data;
}

/** Callable: api("/path") or api("/path", { method, body }). Also api.get/post/... → { data }. */
export async function api(path, options = {}) {
  return request(path, options);
}

function asAxios(data) {
  return { data };
}

api.get = (path, options = {}) => request(path, { ...options, method: "GET" }).then(asAxios);
api.post = (path, body, options = {}) => {
  if (
    body &&
    typeof body === "object" &&
    !Array.isArray(body) &&
    options === undefined &&
    ("params" in body || "headers" in body) &&
    !("email" in body) &&
    !("action" in body) &&
    !("code" in body) &&
    !("query" in body)
  ) {
    return request(path, { method: "POST", ...body }).then(asAxios);
  }
  return request(path, { ...options, method: "POST", body }).then(asAxios);
};
api.put = (path, body, options = {}) =>
  request(path, { ...options, method: "PUT", body }).then(asAxios);
api.delete = (path, options = {}) =>
  request(path, { ...options, method: "DELETE" }).then(asAxios);
api.del = api.delete;

export function apiUrl(path) {
  return `${resolveBaseUrl()}/api${path}`;
}

export const API = `${resolveBaseUrl()}/api`;

export { BASE_URL };
