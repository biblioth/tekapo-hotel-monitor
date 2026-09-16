export async function fetchWithTimeout(fetcher, url, options = {}, timeoutMs = 20_000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort("request timeout"), timeoutMs);
  try {
    return await fetcher(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

export async function expectJson(response, source) {
  if (!response.ok) {
    throw new Error(`${source} returned HTTP ${response.status}`);
  }
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("json")) {
    throw new Error(`${source} returned unexpected content type: ${contentType || "missing"}`);
  }
  return response.json();
}

export function unknown(message) {
  return { status: "unknown", confidence: "unknown", offers: [], message };
}

export function unavailable(message) {
  return { status: "unavailable", confidence: "confirmed", offers: [], message };
}

export function available(offers, message = null, confidence = "confirmed") {
  return { status: "available", confidence, offers, message };
}
