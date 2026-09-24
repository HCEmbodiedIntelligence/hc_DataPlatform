# E offline video dependency

E uses `av==15.1.0` on CPython 3.12 / Linux x86_64, matching the existing isolated LeRobot reader.
The local wheel is repacked without changing package code from `.reader-e/lib/python3.12/site-packages/{av,av.libs,av-15.1.0.dist-info}` using `python -m wheel pack`.
The E Dockerfile installs this wheel offline. Supply the matching wheel here before an E image build; binary wheels are not Git source. Production builds resolve the pinned data extra in `backend/uv.lock`.
See the integration version manifest for the exact local wheel SHA and image ID. The source capture process does not depend on this platform wheel.
