import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, getEnvelope } from "./api";
import type { DataStatus } from "./types";

function mockFetchOnce(response: Partial<Response> & { json?: () => unknown }) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({}),
    ...response,
  } as unknown as Response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const status: DataStatus = {
  source: "mock",
  fetched_at: "2024-01-01T00:00:00Z",
  stale: false,
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("getEnvelope", () => {
  it("unwraps the data_status envelope into { data, status }", async () => {
    const payload = { hello: "world" };
    mockFetchOnce({ json: async () => ({ data: payload, data_status: status }) });

    const result = await getEnvelope<typeof payload>("/thing");

    expect(result.data).toEqual(payload);
    expect(result.status).toEqual(status);
  });

  it("serializes query params onto the URL", async () => {
    const fetchMock = mockFetchOnce({
      json: async () => ({ data: [], data_status: status }),
    });

    await getEnvelope("/stops/near", { lat: 47.5, lon: 19.05, radius: 500 });

    const calledUrl = fetchMock.mock.calls[0][0] as string;
    expect(calledUrl).toContain("/api/stops/near");
    expect(calledUrl).toContain("lat=47.5");
    expect(calledUrl).toContain("lon=19.05");
    expect(calledUrl).toContain("radius=500");
  });

  it("omits null/undefined params", async () => {
    const fetchMock = mockFetchOnce({
      json: async () => ({ data: [], data_status: status }),
    });

    await getEnvelope("/stops/near", { lat: 47.5, lon: 19.05, radius: undefined });

    const calledUrl = fetchMock.mock.calls[0][0] as string;
    expect(calledUrl).not.toContain("radius");
  });

  it("throws ApiError on a non-2xx status", async () => {
    mockFetchOnce({ ok: false, status: 500 });

    await expect(getEnvelope("/thing")).rejects.toMatchObject({
      name: "ApiError",
      status: 500,
    });
  });

  it("throws ApiError with status 0 on a network failure", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error("connection refused"));
    vi.stubGlobal("fetch", fetchMock);

    const error = await getEnvelope("/thing").catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(0);
  });

  it("throws ApiError when the body is missing the envelope shape", async () => {
    mockFetchOnce({ json: async () => ({ not: "an envelope" }) });

    await expect(getEnvelope("/thing")).rejects.toBeInstanceOf(ApiError);
  });
});
