FROM hc-offline/backend:71eaa560 AS worker-dev
ENV PYTHONPATH=/app/src
USER root
COPY vendor/av-15.1.0-cp312-cp312-manylinux_2_28_x86_64.whl /tmp/av-15.1.0-cp312-cp312-manylinux_2_28_x86_64.whl
RUN python -m ensurepip && python -m pip install --no-index --no-deps /tmp/av-15.1.0-cp312-cp312-manylinux_2_28_x86_64.whl
USER 65532:65532
RUN python -c "import av, pytest, pyarrow, lance, psycopg, temporalio"
