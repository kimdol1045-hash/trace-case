import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import {
  TestCasePermanentDeleteDialog,
} from "./test-case-permanent-delete-dialog";
import { isPermanentDeleteConfirmationValid } from "./test-case-permanent-delete-confirmation";

describe("TestCasePermanentDeleteDialog", () => {
  it("requires the exact test-case key before enabling irreversible deletion", () => {
    expect(isPermanentDeleteConfirmationValid("TC-1042", "TC-1042")).toBe(true);
    expect(isPermanentDeleteConfirmationValid("TC-1042", "tc-1042")).toBe(false);
    expect(isPermanentDeleteConfirmationValid("TC-1042", "TC-1042 ")).toBe(false);
  });

  it("explains the irreversible action and starts with confirmation disabled", () => {
    const markup = renderToStaticMarkup(
      <TestCasePermanentDeleteDialog
        open
        caseKey="TC-1042"
        summary="Card checkout"
        onClose={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );

    expect(markup).toContain('role="alertdialog"');
    expect(markup).toContain("복구할 수 없습니다");
    expect(markup).toContain("TC-1042");
    expect(markup).toContain('aria-label="완전 삭제 확인용 테스트케이스 키"');
    expect(markup).toMatch(/<button[^>]*disabled=""[^>]*type="submit"[^>]*>완전 삭제<\/button>/);
  });
});
