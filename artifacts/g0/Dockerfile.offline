FROM hc-offline/backend:71eaa560 AS worker-dev
ENV PYTHONPATH=/app/src
RUN python -c "import pytest, pyarrow, lance, psycopg, temporalio"
