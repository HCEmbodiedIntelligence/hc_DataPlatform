/** Clipboard API is unavailable on HTTP LAN origins. Keep copying usable there. */
export async function copyText(value: string): Promise<boolean> {
  try {
    if (navigator.clipboard) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    /* Use the selected-text fallback when clipboard access is denied. */
  }
  const input = document.createElement("textarea");
  input.value = value;
  input.style.cssText = "position:fixed;left:-9999px;top:0";
  document.body.appendChild(input);
  input.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    input.remove();
  }
}
