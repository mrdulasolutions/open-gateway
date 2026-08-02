const NAME_KEY = "og_name";
const ROLE_KEY = "og_role";

export function getStoredName(): string {
  return localStorage.getItem(NAME_KEY) || "human";
}

export function setStoredName(name: string) {
  localStorage.setItem(NAME_KEY, name);
}

export function getStoredRole(): string {
  return localStorage.getItem(ROLE_KEY) || "observer";
}

export function setStoredRole(role: string) {
  localStorage.setItem(ROLE_KEY, role.trim() || "observer");
}

export function pidKey(roomId: string) {
  return `og_pid_${roomId}`;
}

export function getStoredPid(roomId: string): string | null {
  return localStorage.getItem(pidKey(roomId));
}

export function setStoredPid(roomId: string, id: string) {
  localStorage.setItem(pidKey(roomId), id);
}
