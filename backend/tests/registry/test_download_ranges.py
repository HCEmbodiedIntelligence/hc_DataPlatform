from fastapi import FastAPI, Header
from fastapi.testclient import TestClient
from hc_data_platform.registry.downloads import model_download_response


def test_resume_ranges_preserve_exact_private_model_bytes():
    app = FastAPI()
    @app.get('/model')
    def model(range: str | None = Header(default=None)):
        return model_download_response('preview.glb', (b'01234', b'56789'), range)
    with TestClient(app) as client:
        response = client.get('/model')
        assert response.content == b'0123456789'
        assert response.headers['accept-ranges'] == 'bytes'
        for value, body in [('bytes=3-', b'3456789'), ('bytes=3-6', b'3456'), ('bytes=-2', b'89')]:
            response = client.get('/model', headers={'Range':value})
            assert response.status_code == 206 and response.content == body
            assert response.headers['cache-control'] == 'private, no-store'
        for value in ('bytes=10-', 'bytes=7-4', 'bytes=0-2,4-6', 'invalid'):
            response = client.get('/model', headers={'Range':value})
            assert response.status_code == 416
