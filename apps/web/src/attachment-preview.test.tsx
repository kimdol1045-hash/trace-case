import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { PrivateAttachmentFilenamePreview, PrivateAttachmentPreview } from "./attachment-preview";
import { AttachmentImagePreview, AttachmentLightboxDialog } from "./attachment-image-lightbox";
import { AttachmentTextPreviewDialog } from "./attachment-text-preview";
import { createObjectUrlLease, isImageAttachmentMimeType, isTextAttachmentPreview, loadPrivateAttachmentPreview, readAttachmentText, resolveAttachmentMimeType } from "./attachment-preview-utils";

describe("attachment previews", () => {
  it("limits thumbnails to image MIME types", () => {
    expect(isImageAttachmentMimeType("image/png")).toBe(true);
    expect(isImageAttachmentMimeType("image/webp")).toBe(true);
    expect(isImageAttachmentMimeType("application/pdf")).toBe(false);
  });

  it("recognizes txt and markdown files and normalizes blank browser MIME values", () => {
    expect(isTextAttachmentPreview("notes.txt", "text/plain")).toBe(true);
    expect(isTextAttachmentPreview("README.md", "text/plain")).toBe(true);
    expect(isTextAttachmentPreview("guide.markdown", "text/markdown")).toBe(true);
    expect(isTextAttachmentPreview("report.csv", "text/csv")).toBe(false);
    expect(resolveAttachmentMimeType({ name: "README.md", type: "" })).toBe("text/markdown");
    expect(resolveAttachmentMimeType({ name: "notes.txt", type: "" })).toBe("text/plain");
  });

  it("limits large text previews without losing the visible prefix", async () => {
    const result = await readAttachmentText(new Blob(["abcdef"]), 3);
    expect(result).toEqual({ text: "abc", truncated: true });
  });

  it("revokes each object URL lease exactly once", () => {
    const blob = new Blob(["preview"], { type: "image/png" });
    const urlApi = { createObjectURL: vi.fn(() => "blob:local-preview"), revokeObjectURL: vi.fn() };
    const lease = createObjectUrlLease(blob, urlApi);

    expect(lease.url).toBe("blob:local-preview");
    expect(urlApi.createObjectURL).toHaveBeenCalledWith(blob);
    lease.revoke();
    lease.revoke();
    expect(urlApi.revokeObjectURL).toHaveBeenCalledTimes(1);
    expect(urlApi.revokeObjectURL).toHaveBeenCalledWith("blob:local-preview");
  });

  it("creates registered-image previews only from authenticated blob responses", async () => {
    const blob = new Blob(["private"], { type: "image/png" });
    const controller = new AbortController();
    const loadBlob = vi.fn(async () => blob);
    const urlApi = { createObjectURL: vi.fn(() => "blob:private-preview"), revokeObjectURL: vi.fn() };
    const lease = await loadPrivateAttachmentPreview("attachment_01", loadBlob, controller.signal, urlApi);

    expect(loadBlob).toHaveBeenCalledWith("attachment_01", controller.signal);
    expect(lease?.url).toBe("blob:private-preview");
    expect(lease?.url).not.toContain("/api/attachments/");
    expect(urlApi.createObjectURL).toHaveBeenCalledWith(blob);
  });

  it("does not create a URL when a private preview request is aborted", async () => {
    const controller = new AbortController();
    const urlApi = { createObjectURL: vi.fn(() => "blob:unused"), revokeObjectURL: vi.fn() };
    const loadBlob = vi.fn(async () => {
      controller.abort();
      return new Blob(["late"], { type: "image/png" });
    });

    await expect(loadPrivateAttachmentPreview("attachment_02", loadBlob, controller.signal, urlApi)).resolves.toBeNull();
    expect(urlApi.createObjectURL).not.toHaveBeenCalled();
  });

  it("renders an accessible loading state without exposing a private URL", () => {
    const markup = renderToStaticMarkup(<PrivateAttachmentPreview
      attachment={{ id: "attachment_03", filename: "login.png", mimeType: "image/png", resourceVersion: 1 }}
      loadBlob={async () => new Blob()}
    />);

    expect(markup).toContain("비공개 미리보기 불러오는 중");
    expect(markup).not.toContain("/api/attachments/");
    expect(markup).not.toContain("proxyPath");
  });

  it("keeps non-image attachments as file rows without a thumbnail request", () => {
    const loadBlob = vi.fn(async () => new Blob());
    const markup = renderToStaticMarkup(<PrivateAttachmentPreview
      attachment={{ id: "attachment_04", filename: "report.pdf", mimeType: "application/pdf", resourceVersion: 1 }}
      loadBlob={loadBlob}
    />);

    expect(markup).toContain("tm-attachment-preview is-file");
    expect(markup).not.toContain("<img");
    expect(markup).not.toContain("불러오는 중");
    expect(loadBlob).not.toHaveBeenCalled();
  });

  it("renders registered markdown attachments as lazy dialog triggers", () => {
    const loadBlob = vi.fn(async () => new Blob(["# Release notes"], { type: "text/markdown" }));
    const markup = renderToStaticMarkup(<PrivateAttachmentPreview
      attachment={{ id: "attachment_05", filename: "release.md", mimeType: "text/markdown", resourceVersion: 1 }}
      loadBlob={loadBlob}
    />);

    expect(markup).toContain("release.md 내용 보기");
    expect(markup).toContain('aria-haspopup="dialog"');
    expect(markup).toContain(">MD</button>");
    expect(loadBlob).not.toHaveBeenCalled();
  });

  it("makes a registered text filename open the same lazy preview dialog", () => {
    const loadBlob = vi.fn(async () => new Blob(["blocked"], { type: "text/plain" }));
    const markup = renderToStaticMarkup(<PrivateAttachmentFilenamePreview
      attachment={{ id: "attachment_06", filename: "shipping-blocked.txt", mimeType: "text/plain", resourceVersion: 1 }}
      loadBlob={loadBlob}
    />);

    expect(markup).toContain("shipping-blocked.txt 내용 보기");
    expect(markup).toContain("tm-attachment-filename-preview");
    expect(markup).toContain(">shipping-blocked.txt</button>");
    expect(loadBlob).not.toHaveBeenCalled();
  });

  it("renders text contents in an accessible modal", () => {
    const markup = renderToStaticMarkup(<AttachmentTextPreviewDialog
      filename="release.md"
      open
      state={{ phase: "ready", text: "# Release notes\nPASS", truncated: false }}
      onClose={() => undefined}
    />);

    expect(markup).toContain('role="dialog"');
    expect(markup).toContain("텍스트 미리보기 닫기");
    expect(markup).toContain("# Release notes");
    expect(markup).toContain("PASS");
  });

  it("renders image thumbnails as accessible dialog triggers", () => {
    const markup = renderToStaticMarkup(<AttachmentImagePreview filename="login.png" url="blob:private-preview" />);

    expect(markup).toContain("login.png 크게 보기");
    expect(markup).toContain('aria-haspopup="dialog"');
    expect(markup).toContain('class="tm-attachment-preview is-ready"');
  });

  it("renders an accessible large-image dialog", () => {
    const markup = renderToStaticMarkup(<AttachmentLightboxDialog
      filename="login.png"
      url="blob:private-preview"
      open
      onClose={() => undefined}
    />);

    expect(markup).toContain('role="dialog"');
    expect(markup).toContain('aria-modal="true"');
    expect(markup).toContain("login.png 확대 이미지");
    expect(markup).toContain("이미지 미리보기 닫기");
  });
});
