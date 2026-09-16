const MINUTE_MS = 60_000;

export function backoffDelayMs(consecutiveUnknown) {
  if (consecutiveUnknown <= 0) return 0;
  if (consecutiveUnknown === 1) return 15 * MINUTE_MS;
  if (consecutiveUnknown === 2) return 60 * MINUTE_MS;
  return 6 * 60 * MINUTE_MS;
}

export function backoffDecision(snapshot, now = new Date()) {
  const failures = Number(snapshot?.consecutive_unknown || 0);
  const delayMs = backoffDelayMs(failures);
  if (!snapshot || delayMs === 0) {
    return { shouldCheck: true, failures, nextCheckAt: null };
  }

  const observedAt = new Date(snapshot.observed_at);
  if (Number.isNaN(observedAt.getTime())) {
    return { shouldCheck: true, failures, nextCheckAt: null };
  }
  const nextCheckAt = new Date(observedAt.getTime() + delayMs);
  return {
    shouldCheck: now.getTime() >= nextCheckAt.getTime(),
    failures,
    nextCheckAt: nextCheckAt.toISOString(),
  };
}
