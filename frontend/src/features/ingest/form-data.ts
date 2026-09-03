export function formRawText(data: FormData, key: string): string {
  const value = data.get(key);
  return typeof value === 'string' ? value : '';
}

export function formText(data: FormData, key: string): string {
  return formRawText(data, key).trim();
}
