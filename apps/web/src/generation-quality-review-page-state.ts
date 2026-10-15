export function qualityReviewListCountLabel(
  page: { items: unknown[] } | null,
  state: "loading" | "ready" | "error",
) {
  return page
    ? `현재 ${page.items.length}건`
    : state === "error"
      ? "목록 이용 불가"
      : "불러오는 중";
}
