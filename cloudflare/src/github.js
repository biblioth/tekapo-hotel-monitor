export async function dispatchBrowserValidation(env, payload, fetcher = fetch) {
  if (!env.GITHUB_TOKEN || env.SHADOW_MODE === "true") return false;
  const owner = env.GITHUB_OWNER || "biblioth";
  const repo = env.GITHUB_REPO || "tekapo-hotel-monitor";
  const response = await fetcher(
    `https://api.github.com/repos/${owner}/${repo}/dispatches`,
    {
      method: "POST",
      headers: {
        accept: "application/vnd.github+json",
        authorization: `Bearer ${env.GITHUB_TOKEN}`,
        "content-type": "application/json",
        "user-agent": "LakeWatch-Cloudflare-Sensor",
        "x-github-api-version": "2022-11-28",
      },
      body: JSON.stringify({
        event_type: "lakewatch-browser-check",
        client_payload: payload,
      }),
    },
  );
  if (response.status !== 204) {
    throw new Error(`GitHub dispatch returned HTTP ${response.status}`);
  }
  return true;
}
