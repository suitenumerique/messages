import { CapacitorHttp } from "@capacitor/core";

import { nativeFetch } from "./fetch";

vi.mock("@capacitor/core", () => ({
  CapacitorHttp: { request: vi.fn() },
}));

const http = vi.mocked(CapacitorHttp);

beforeEach(() => {
  vi.clearAllMocks();
  http.request.mockResolvedValue({
    status: 200,
    data: { ok: true },
    headers: { "Content-Type": "application/json" },
    url: "",
  });
});

describe("nativeFetch", () => {
  it("passes method, url and headers verbatim — Origin included", async () => {
    await nativeFetch("http://api.test/threads/", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": "tok",
        Origin: "http://api.test",
      },
      body: '{"subject":"hello"}',
    });

    expect(http.request).toHaveBeenCalledWith({
      url: "http://api.test/threads/",
      method: "POST",
      // Headers iteration lowercases names; the wire and Django are
      // case-insensitive, but the Origin must not have been dropped.
      headers: {
        "content-type": "application/json",
        "x-csrftoken": "tok",
        origin: "http://api.test",
      },
      data: '{"subject":"hello"}',
    });
  });

  it("flattens FormData bodies to the plugin entry list and drops Content-Type", async () => {
    const formData = new FormData();
    formData.append("name", "report");
    formData.append(
      "file",
      new File(["hello"], "report.txt", { type: "text/plain" }),
    );

    await nativeFetch("http://api.test/blob/", {
      method: "POST",
      headers: { "content-type": "multipart/form-data" },
      body: formData,
    });

    expect(http.request).toHaveBeenCalledWith({
      url: "http://api.test/blob/",
      method: "POST",
      headers: {},
      data: [
        { key: "name", value: "report", type: "string" },
        {
          key: "file",
          value: "aGVsbG8=",
          type: "base64File",
          contentType: "text/plain",
          fileName: "report.txt",
        },
      ],
      dataType: "formData",
    });
  });

  it("rebuilds a standard Response from the parsed plugin payload", async () => {
    http.request.mockResolvedValue({
      status: 201,
      data: { id: "42" },
      headers: { "Content-Type": "application/json" },
      url: "",
    });

    const response = await nativeFetch("http://api.test/threads/", {
      method: "POST",
      body: "{}",
    });

    expect(response.ok).toBe(true);
    expect(response.status).toBe(201);
    expect(response.headers.get("Content-Type")).toBe("application/json");
    await expect(response.json()).resolves.toEqual({ id: "42" });
  });

  it("maps 204 to a body-less Response", async () => {
    http.request.mockResolvedValue({
      status: 204,
      data: undefined,
      headers: {},
      url: "",
    });

    const response = await nativeFetch("http://api.test/threads/1/", {
      method: "DELETE",
    });

    expect(response.status).toBe(204);
    await expect(response.text()).resolves.toBe("");
  });

  it("resolves on HTTP errors like fetch does, keeping the error body", async () => {
    http.request.mockResolvedValue({
      status: 403,
      data: { detail: "CSRF Failed" },
      headers: { "Content-Type": "application/json" },
      url: "",
    });

    const response = await nativeFetch("http://api.test/threads/", {
      method: "POST",
      body: "{}",
    });

    expect(response.ok).toBe(false);
    expect(response.status).toBe(403);
    await expect(response.json()).resolves.toEqual({ detail: "CSRF Failed" });
  });

  it("rejects unsupported body types instead of corrupting the request", async () => {
    await expect(
      nativeFetch("http://api.test/blob/", {
        method: "POST",
        body: new Blob(["raw"]),
      }),
    ).rejects.toThrow("nativeFetch only supports string and FormData bodies.");
    expect(http.request).not.toHaveBeenCalled();
  });
});
