export interface BreadcrumbItem {
  id: string;
  label: string;
}

export interface BreadcrumbsProps {
  items: BreadcrumbItem[];
  onSelect?: (id: string) => void;
  rootLabel?: string;
}

export function Breadcrumbs({ items, onSelect, rootLabel = "Root" }: BreadcrumbsProps) {
  return (
    <nav className="breadcrumbs" aria-label="폴더 경로">
      <ol>
        <li>
          <span>{rootLabel}</span>
        </li>
        {items.map((item, index) => {
          const current = index === items.length - 1;
          return (
            <li key={item.id} aria-current={current ? "page" : undefined}>
              <span className="breadcrumbs__separator" aria-hidden="true">
                /
              </span>
              {onSelect && !current ? (
                <button type="button" onClick={() => onSelect(item.id)}>
                  {item.label}
                </button>
              ) : (
                <span>{item.label}</span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
