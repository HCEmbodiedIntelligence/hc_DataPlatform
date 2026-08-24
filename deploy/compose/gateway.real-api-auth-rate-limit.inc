# Included inside the public gateway server only for the real-API test overlay.
# Normal interactive logins fit within the burst; a bounded abuse probe receives
# a standards-shaped 429 without exposing backend headers or internals.
location = /api/v1/auth/sessions {
    limit_req zone=real_api_auth_sessions burst=8 nodelay;
    limit_req_status 429;
    error_page 429 = @real_api_rate_limited;

    proxy_pass $api_upstream$request_uri;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location @real_api_rate_limited {
    internal;
    default_type application/problem+json;
    add_header Cache-Control "no-store" always;
    add_header Retry-After "1" always;
    return 429 '{"type":"https://hc-data-platform.invalid/problems/rate-limited","title":"Too Many Requests","status":429,"code":"RATE_LIMITED","detail":"Authentication request rate limit exceeded."}';
}
