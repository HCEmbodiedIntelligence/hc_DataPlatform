"""Byte ranges for already authorized model downloads (including private previews)."""
import mimetypes
import re
from urllib.parse import quote

from fastapi.responses import Response, StreamingResponse


def model_download_response(filename, chunks, range_header=None):
    chunks = tuple(chunks)
    size = sum(map(len, chunks))
    headers = {"Cache-Control": "private, no-store", "Accept-Ranges": "bytes",
               "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
               "X-Content-Type-Options": "nosniff"}
    start, end, status = 0, size - 1, 200
    if range_header:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
        if match and any(match.groups()):
            left, right = match.groups()
            if left:
                start = int(left)
                end = min(int(right), size - 1) if right else size - 1
            else:
                start = max(0, size - int(right))
        else:
            start = size
        if start > end or start >= size:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        status = 206
    headers["Content-Length"] = str(max(0, end - start + 1))

    def selected():
        offset = 0
        for chunk in chunks:
            left, right = max(0, start - offset), min(len(chunk), end + 1 - offset)
            if right > left:
                yield chunk[left:right]
            offset += len(chunk)
            if offset > end:
                break

    return StreamingResponse(selected(), status_code=status, headers=headers,
                             media_type=mimetypes.guess_type(filename)[0] or "application/octet-stream")
