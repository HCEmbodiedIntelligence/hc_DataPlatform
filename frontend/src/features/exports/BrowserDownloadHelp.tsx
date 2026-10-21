export function BrowserDownloadHelp() {
  return (
    <details>
      <summary>Chrome 没有开始下载？</summary>
      <p>
        按 Ctrl+J 打开下载记录。若这个 ZIP 提示“非安全下载”，可在该条记录的
        “更多操作”中选择“保留”或“下载不安全的文件”。
      </p>
      <p>
        如果这是你信任的内网平台，可以点击网址左侧图标 → 网站设置，将
        “不安全内容”设为“允许”，然后刷新平台重新下载。此设置只针对当前网站，
        Windows 和 Linux 的 Chrome 操作相同。
      </p>
    </details>
  );
}
