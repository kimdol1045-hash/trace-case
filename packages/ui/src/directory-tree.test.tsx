import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { DirectoryTree, type DirectoryTreeItem } from "./directory-tree";

describe("DirectoryTree", () => {
  it("renders four visible levels with counts and a protected system marker", () => {
    const leaf: DirectoryTreeItem = {
      id: "dormant",
      name: "휴면 계정",
      depth: 3,
      directCount: 2,
      subtreeCount: 2,
      hasChildren: false,
    };
    const items: DirectoryTreeItem[] = [
      {
        id: "product",
        name: "제품 영역",
        depth: 0,
        directCount: 3,
        subtreeCount: 15,
        hasChildren: true,
        isExpanded: true,
        children: [
          {
            id: "members",
            name: "회원",
            depth: 1,
            directCount: 4,
            subtreeCount: 12,
            hasChildren: true,
            isExpanded: true,
            children: [
              {
                id: "accounts",
                name: "계정 관리",
                depth: 2,
                directCount: 6,
                subtreeCount: 8,
                hasChildren: true,
                isExpanded: true,
                children: [leaf],
              },
            ],
          },
        ],
      },
      {
        id: "ai",
        name: "AI 생성함",
        depth: 0,
        directCount: 8,
        subtreeCount: 8,
        hasChildren: false,
        isProtected: true,
      },
    ];

    const markup = renderToStaticMarkup(
      <DirectoryTree items={items} selectedId="dormant" onSelect={vi.fn()} onToggle={vi.fn()} />,
    );

    expect(markup).toContain('data-depth="3"');
    expect(markup).toContain("휴면 계정");
    expect(markup).toContain("직접 2, 전체 2");
    expect(markup).toContain("SYSTEM");
    expect(markup).toContain('data-mobile-layout="scroll"');
  });

  it("renders lazy-loading and retry states inside an expanded branch", () => {
    const base: DirectoryTreeItem = {
      id: "members",
      name: "회원",
      depth: 0,
      directCount: 1,
      subtreeCount: 3,
      hasChildren: true,
      isExpanded: true,
      isLoading: true,
    };
    expect(
      renderToStaticMarkup(
        <DirectoryTree items={[base]} onSelect={vi.fn()} onToggle={vi.fn()} />,
      ),
    ).toContain("하위 폴더 불러오는 중");

    const markup = renderToStaticMarkup(
      <DirectoryTree
        items={[{ ...base, isLoading: false, loadError: "불러오기 실패" }]}
        onSelect={vi.fn()}
        onToggle={vi.fn()}
        onRetry={vi.fn()}
      />,
    );
    expect(markup).toContain("불러오기 실패");
    expect(markup).toContain("다시 시도");
  });
});
