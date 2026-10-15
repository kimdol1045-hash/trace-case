export interface AttachmentBatchResult<T> {
  completed: T[];
  failed: Array<{ item: T; error: unknown }>;
}

/** Runs every upload and returns a retry-safe list containing only failures. */
export async function uploadAttachmentBatch<T>(
  items: readonly T[],
  upload: (item: T, index: number, total: number) => Promise<void>,
): Promise<AttachmentBatchResult<T>> {
  const completed: T[] = [];
  const failed: Array<{ item: T; error: unknown }> = [];
  for (const [index, item] of items.entries()) {
    try {
      await upload(item, index, items.length);
      completed.push(item);
    } catch (error) {
      failed.push({ item, error });
    }
  }
  return { completed, failed };
}
