# Isolated dependency-layer extension; production images install the pinned data extra.
FROM openarm-e-backend:integration
USER root
COPY .g1-artifacts/realsense-wheels/pyrealsense2-2.58.4.10922-cp312-cp312-manylinux1_x86_64.whl /tmp/pyrealsense2-2.58.4.10922-cp312-cp312-manylinux1_x86_64.whl
RUN echo '1e83454cbaf9de50962d78ce3addb0b6a6d8d02259c1f0c64d4a0d527a02bf73  /tmp/pyrealsense2-2.58.4.10922-cp312-cp312-manylinux1_x86_64.whl' | sha256sum -c - \
    && /opt/venv/bin/python -m pip install --no-index --no-deps /tmp/pyrealsense2-2.58.4.10922-cp312-cp312-manylinux1_x86_64.whl
USER 65532:65532
