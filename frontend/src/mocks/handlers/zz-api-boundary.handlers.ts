import { http, HttpResponse } from "msw";

/**
 * Browser Mock must be hermetic. An unhandled API request is a missing fixture,
 * not permission to fall through to whichever real API happens to be running.
 */
export default [
  http.all(/\/api\/v1\/.*/u, ({ request }) => {
    const url = new URL(request.url);
    return HttpResponse.json(
      {
        type: "about:blank",
        title: "Browser Mock handler missing",
        status: 501,
        detail: `Browser Mock has no handler for ${request.method} ${url.pathname}.`,
        code: "MOCK_HANDLER_MISSING",
        request_id: "req_mock_handler_missing",
        retryable: false,
      },
      {
        status: 501,
        headers: { "X-HC-Mock-Unhandled": "true" },
      },
    );
  }),
];
