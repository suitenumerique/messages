import { CapacitorHttp } from "@capacitor/core";
import { Filesystem } from "@capacitor/filesystem";
import { Share } from "@capacitor/share";

import { nativeDownloadFile } from "./download";

vi.mock("@capacitor/core", () => ({
  CapacitorHttp: { get: vi.fn() },
}));
vi.mock("@capacitor/filesystem", () => ({
  Directory: { Cache: "CACHE" },
  Filesystem: { writeFile: vi.fn() },
}));
vi.mock("@capacitor/share", () => ({
  Share: { share: vi.fn() },
}));

const http = vi.mocked(CapacitorHttp);
const filesystem = vi.mocked(Filesystem);
const share = vi.mocked(Share);

beforeEach(() => {
  vi.clearAllMocks();
  http.get.mockResolvedValue({ status: 200, data: "aGVsbG8=", headers: {}, url: "" });
  filesystem.writeFile.mockResolvedValue({ uri: "file:///cache/report.pdf" });
  share.share.mockResolvedValue({ activityType: undefined });
});

describe("nativeDownloadFile", () => {
  it("writes the fetched bytes to cache and hands them to the share sheet", async () => {
    await nativeDownloadFile("http://api.test/blob/1", "report.pdf");

    expect(http.get).toHaveBeenCalledWith({
      url: "http://api.test/blob/1",
      responseType: "blob",
    });
    expect(filesystem.writeFile).toHaveBeenCalledWith({
      path: "report.pdf",
      data: "aGVsbG8=",
      directory: "CACHE",
    });
    expect(share.share).toHaveBeenCalledWith({
      url: "file:///cache/report.pdf",
      title: "report.pdf",
    });
  });

  it("strips path traversal sequences from the sender-controlled filename", async () => {
    // Attachment names come from MIME headers, so a crafted name must not be
    // able to escape the cache directory.
    await nativeDownloadFile("http://api.test/blob/1", "../../etc/passwd");

    expect(filesystem.writeFile).toHaveBeenCalledWith(
      expect.objectContaining({ path: "passwd" }),
    );
  });

  it("falls back to a default name when sanitization leaves nothing", async () => {
    await nativeDownloadFile("http://api.test/blob/1", "..\\..\\");

    expect(filesystem.writeFile).toHaveBeenCalledWith(
      expect.objectContaining({ path: "download" }),
    );
  });

  it("rejects on an HTTP error status instead of sharing the error body", async () => {
    // CapacitorHttp resolves on 4xx/5xx; without the guard a 401 body would be
    // written to disk and shared as if it were the file.
    http.get.mockResolvedValue({ status: 401, data: "denied", headers: {}, url: "" });

    await expect(
      nativeDownloadFile("http://api.test/blob/1", "report.pdf"),
    ).rejects.toThrow("401");
    expect(filesystem.writeFile).not.toHaveBeenCalled();
  });

  it("treats a dismissed share sheet as a success", async () => {
    share.share.mockRejectedValue(new Error("Share canceled"));

    await expect(
      nativeDownloadFile("http://api.test/blob/1", "report.pdf"),
    ).resolves.toBeUndefined();
  });

  it("propagates genuine share failures", async () => {
    share.share.mockRejectedValue(new Error("No activity available"));

    await expect(
      nativeDownloadFile("http://api.test/blob/1", "report.pdf"),
    ).rejects.toThrow("No activity available");
  });
});
