const storageKey = () =>
  `og.pinned-rooms:${typeof window !== "undefined" ? window.location.origin : "local"}`;

export function loadPinnedRoomIds(): string[] {
  try {
    const raw = localStorage.getItem(storageKey());
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((id): id is string => typeof id === "string");
  } catch {
    return [];
  }
}

export function savePinnedRoomIds(ids: string[]) {
  try {
    localStorage.setItem(storageKey(), JSON.stringify(ids));
  } catch {
    /* ignore */
  }
}

export function togglePinnedRoom(roomId: string): string[] {
  const set = new Set(loadPinnedRoomIds());
  if (set.has(roomId)) set.delete(roomId);
  else set.add(roomId);
  const next = Array.from(set);
  savePinnedRoomIds(next);
  return next;
}
