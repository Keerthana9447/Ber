import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, buildUrl, isEndpointMissing, messageFromDetail, tokenStore } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
  tokenStore.clear();
});

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("api helpers", () => {
  it("builds query strings and skips empty values", () => {
    expect(buildUrl("/x", { page: 2, q: "", a: undefined, b: null, c: "z" })).toBe("/api/v1/x?page=2&c=z");
    expect(buildUrl("/x")).toBe("/api/v1/x");
  });
  it("extracts messages from FastAPI error details", () => {
    expect(messageFromDetail("Nope", "f")).toBe("Nope");
    expect(messageFromDetail([{ msg: "a" }, { msg: "b" }], "f")).toBe("a; b");
    expect(messageFromDetail(undefined, "fallback")).toBe("fallback");
  });
  it("detects missing endpoints", () => {
    expect(isEndpointMissing(new ApiError(404, "x"))).toBe(true);
    expect(isEndpointMissing(new ApiError(403, "x"))).toBe(false);
    expect(isEndpointMissing(new Error("x"))).toBe(false);
  });
  it("sends the bearer token and parses JSON", async () => {
    tokenStore.setAccess("tok");
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(200, { ok: 1 }));
    await expect(api("/health")).resolves.toEqual({ ok: 1 });
    const headers = (spy.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer tok");
  });
  it("turns error responses into ApiError", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json(403, { detail: "Insufficient permissions" }));
    await expect(api("/users")).rejects.toMatchObject({ status: 403, message: "Insufficient permissions" });
  });
  it("refreshes once on 401 and retries", async () => {
    tokenStore.setAccess("old");
    tokenStore.setRefresh("r1");
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json(401, { detail: "Authentication required" }))
      .mockResolvedValueOnce(json(200, { access_token: "new", refresh_token: "r2", token_type: "bearer", expires_at: "x" }))
      .mockResolvedValueOnce(json(200, { done: true }));
    await expect(api("/customers")).resolves.toEqual({ done: true });
    expect(spy).toHaveBeenCalledTimes(3);
    expect(tokenStore.getRefresh()).toBe("r2");
  });
  it("reports an unreachable server clearly", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(api("/health")).rejects.toMatchObject({ status: 0 });
  });
});
