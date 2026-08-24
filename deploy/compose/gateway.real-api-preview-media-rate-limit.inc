# The HLS media gateway intentionally accepts a short-lived URL capability: a
# video element cannot attach the Bearer token that authorized descriptor
# issuance. Limit this public-shaped route separately from login so normal HLS
# startup (playlist + init + several segments) is not starved by auth traffic.
location ~ "^/api/v1/previews/sessions/[^/]+/media/(?:index\.m3u8|init\.mp4|segment_[0-9]{5}\.m4s)$" {
    limit_req zone=real_api_preview_media burst=32 nodelay;
    limit_conn real_api_preview_media_conn 8;
    limit_req_status 429;
    limit_conn_status 429;
    error_page 429 = @real_api_preview_media_rate_limited;

    proxy_pass $api_upstream$request_uri;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location @real_api_preview_media_rate_limited {
    internal;
    default_type application/problem+json;
    add_header Cache-Control "no-store" always;
    add_header Retry-After "1" always;
    return 429 '{"type":"https://hc-data-platform.invalid/problems/rate-limited","title":"Too Many Requests","status":429,"code":"RATE_LIMITED","detail":"Preview media request rate limit exceeded."}';
}
