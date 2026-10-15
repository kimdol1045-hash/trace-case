import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { DeleteConfirmDialog } from "./delete-confirm-dialog";

describe("DeleteConfirmDialog", () => {
  it("uses the shared accessible modal design for destructive actions", () => {
    const markup = renderToStaticMarkup(<DeleteConfirmDialog
      open
      title="사이클 삭제"
      entityName="배송 회귀 테스트"
      description="실행 결과와 이슈 이력은 보존됩니다."
      onClose={() => undefined}
      onConfirm={() => undefined}
    />);

    expect(markup).toContain('role="alertdialog"');
    expect(markup).toContain('aria-modal="true"');
    expect(markup).toContain("배송 회귀 테스트");
    expect(markup).toContain("실행 결과와 이슈 이력은 보존됩니다.");
    expect(markup).toContain(">취소</button>");
    expect(markup).toContain("tm-delete-confirm-button");
  });
});
