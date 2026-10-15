import { describe, expect, it, vi } from "vitest";

import { uploadAttachmentBatch } from "./attachment-batch";

describe("uploadAttachmentBatch", () => {
  it("continues after a partial failure and retries only failed items", async () => {
    const upload = vi.fn(async (name: string) => {
      if (name === "bad.txt") throw new Error("quarantined");
    });
    const first = await uploadAttachmentBatch(["one.txt", "bad.txt", "two.txt"], upload);
    expect(first.completed).toEqual(["one.txt", "two.txt"]);
    expect(first.failed.map(({ item }) => item)).toEqual(["bad.txt"]);
    expect(upload).toHaveBeenCalledTimes(3);

    upload.mockImplementation(async () => undefined);
    const retry = await uploadAttachmentBatch(first.failed.map(({ item }) => item), upload);
    expect(retry.completed).toEqual(["bad.txt"]);
    expect(retry.failed).toEqual([]);
    expect(upload).toHaveBeenCalledTimes(4);
  });
});
