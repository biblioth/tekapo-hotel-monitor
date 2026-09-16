function offerIdentity(offer) {
  return String(offer.identity || offer.roomName || "").trim().toLocaleLowerCase("en");
}

export function applyObservation(previous, observation) {
  if (observation.status === "unknown") {
    if (!previous) {
      return {
        snapshot: {
          status: "unknown",
          offers: [],
          consecutiveUnknown: 1,
        },
        event: null,
      };
    }
    return {
      snapshot: {
        ...previous,
        consecutiveUnknown: (previous.consecutiveUnknown || 0) + 1,
      },
      event: null,
    };
  }

  const snapshot = {
    status: observation.status,
    offers: observation.offers || [],
    consecutiveUnknown: 0,
  };
  if (!previous || previous.status === "unknown" || observation.status === "unavailable") {
    return { snapshot, event: null };
  }

  if (previous.status === "unavailable" && observation.status === "available") {
    return {
      snapshot,
      event: { type: "availability_returned", offers: observation.offers || [] },
    };
  }

  if (previous.status === "available" && observation.status === "available") {
    const oldIds = new Set((previous.offers || []).map(offerIdentity));
    const newOffers = (observation.offers || []).filter(
      (offer) => !oldIds.has(offerIdentity(offer)),
    );
    if (newOffers.length > 0) {
      return { snapshot, event: { type: "new_room", offers: newOffers } };
    }
  }
  return { snapshot, event: null };
}
