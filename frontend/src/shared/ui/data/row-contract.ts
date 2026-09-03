export function validateDataTableRows<TData>(
  data: readonly TData[],
  getRowId: (row: TData) => string,
): TData[] {
  const rowIds = new Set<string>();
  return data.map((row) => {
    const rowId = getRowId(row);
    if (rowId.trim().length === 0) {
      throw new Error('DataTable requires every row to have a non-empty stable row ID');
    }
    if (rowIds.has(rowId)) {
      throw new Error(`DataTable received duplicate stable row ID: ${rowId}`);
    }
    rowIds.add(rowId);
    return row;
  });
}
