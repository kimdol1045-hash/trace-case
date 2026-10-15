import type { CSSProperties } from "react";

export interface DirectoryTreeItem {
  id: string;
  name: string;
  depth: number;
  directCount: number;
  subtreeCount: number;
  hasChildren: boolean;
  isExpanded?: boolean;
  isLoading?: boolean;
  loadError?: string;
  isProtected?: boolean;
  children?: DirectoryTreeItem[];
}

export interface DirectoryTreeProps {
  items: DirectoryTreeItem[];
  selectedId?: string | null;
  label?: string;
  onSelect: (id: string) => void;
  onToggle: (id: string) => void;
  onRetry?: (id: string) => void;
}

export function DirectoryTree({
  items,
  selectedId,
  label = "디렉터리",
  onSelect,
  onToggle,
  onRetry,
}: DirectoryTreeProps) {
  return (
    <ul className="directory-tree" role="tree" aria-label={label} data-mobile-layout="scroll">
      {items.map((item) => (
        <DirectoryTreeNode
          item={item}
          key={item.id}
          selectedId={selectedId}
          onSelect={onSelect}
          onToggle={onToggle}
          onRetry={onRetry}
        />
      ))}
    </ul>
  );
}

interface DirectoryTreeNodeProps extends Pick<
  DirectoryTreeProps,
  "selectedId" | "onSelect" | "onToggle" | "onRetry"
> {
  item: DirectoryTreeItem;
}

function DirectoryTreeNode({
  item,
  selectedId,
  onSelect,
  onToggle,
  onRetry,
}: DirectoryTreeNodeProps) {
  const style = { "--tree-depth": item.depth } as CSSProperties;
  return (
    <li
      className="directory-tree__item"
      role="treeitem"
      aria-expanded={item.hasChildren ? Boolean(item.isExpanded) : undefined}
      aria-selected={selectedId === item.id}
    >
      <div
        className={`directory-tree__row${
          selectedId === item.id ? " directory-tree__row--selected" : ""
        }`}
        style={style}
        data-depth={item.depth}
      >
        {item.hasChildren ? (
          <button
            className="directory-tree__toggle"
            type="button"
            aria-label={`${item.name} ${item.isExpanded ? "접기" : "펼치기"}`}
            onClick={() => onToggle(item.id)}
          >
            <span aria-hidden="true">{item.isExpanded ? "−" : "+"}</span>
          </button>
        ) : (
          <span className="directory-tree__toggle directory-tree__toggle--empty" aria-hidden="true">
            ·
          </span>
        )}
        <button
          className="directory-tree__select"
          type="button"
          onClick={() => onSelect(item.id)}
        >
          <span className="directory-tree__folder" aria-hidden="true" />
          <span className="directory-tree__name">{item.name}</span>
          {item.isProtected ? <span className="directory-tree__system">SYSTEM</span> : null}
          <span className="directory-tree__counts" aria-label={`직접 ${item.directCount}, 전체 ${item.subtreeCount}`}>
            {item.directCount} / {item.subtreeCount}
          </span>
        </button>
      </div>
      {item.isExpanded ? (
        <div className="directory-tree__branch">
          {item.isLoading ? (
            <p className="directory-tree__status" role="status">
              하위 폴더 불러오는 중…
            </p>
          ) : null}
          {item.loadError ? (
            <p className="directory-tree__status directory-tree__status--error" role="alert">
              {item.loadError}
              {onRetry ? (
                <button type="button" onClick={() => onRetry(item.id)}>
                  다시 시도
                </button>
              ) : null}
            </p>
          ) : null}
          {!item.isLoading && !item.loadError && item.children?.length === 0 ? (
            <p className="directory-tree__status">하위 폴더가 없습니다.</p>
          ) : null}
          {item.children && item.children.length > 0 ? (
            <ul role="group">
              {item.children.map((child) => (
                <DirectoryTreeNode
                  item={child}
                  key={child.id}
                  selectedId={selectedId}
                  onSelect={onSelect}
                  onToggle={onToggle}
                  onRetry={onRetry}
                />
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}
