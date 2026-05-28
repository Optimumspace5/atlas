/**
 * Tiny API client for the Atlas FastAPI backend.
 *
 * NEXT_PUBLIC_API_URL is read at build time (and at request time on the
 * client). Default falls back to the dev-mode localhost backend.
 */
export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type HealthResponse = {
  status: string;
};

export async function getHealth(): Promise<HealthResponse> {
  const r = await fetch(`${API_URL}/health`);
  if (!r.ok) {
    throw new Error(`Health check failed: ${r.status}`);
  }
  return r.json();
}
