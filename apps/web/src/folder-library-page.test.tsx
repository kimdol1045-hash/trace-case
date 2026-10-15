import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { FolderLibraryPage } from "./folder-library-page";
import { TestManagementProvider } from "./test-management-demo";

describe("FolderLibraryPage test-case tabs", () => {
  it("separates active and deleted test cases into accessible tabs", () => {
    const markup = renderToStaticMarkup(
      <MemoryRouter>
        <TestManagementProvider enableDevelopmentFixtures>
          <FolderLibraryPage />
        </TestManagementProvider>
      </MemoryRouter>,
    );

    expect(markup).toContain('role="tablist"');
    expect(markup).toContain('aria-label="테스트케이스 목록 상태"');
    expect(markup).toContain('aria-selected="true">활성</button>');
    expect(markup).toContain('aria-selected="false" title="삭제 항목 포함 대신 삭제된 항목만 표시">삭제됨</button>');
    expect(markup).not.toContain('type="checkbox"');
  });
});
