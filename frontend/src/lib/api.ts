export async function readJsonResponse<T>(response: Response): Promise<T> {
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().includes("application/json")) {
    throw new Error(apiResponseError(response));
  }
  return await response.json() as T;
}

function apiResponseError(response: Response): string {
  if (response.status === 404) {
    return "The dashboard API is not available. Make sure the orchestrator log UI is running.";
  }
  return response.ok
    ? "The dashboard API returned a non-JSON response."
    : `The dashboard API returned HTTP ${response.status}.`;
}
