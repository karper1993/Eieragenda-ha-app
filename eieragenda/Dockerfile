ARG BUILD_FROM
FROM ${BUILD_FROM}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apk add --no-cache \
    python3 \
    py3-pip \
    jq \
  && python3 -m venv /opt/eieragenda-venv

ENV PATH="/opt/eieragenda-venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EIERAGENDA_PORT=8099 \
    EIERAGENDA_DB_PATH=/share/eieragenda/eieragenda.db \
    EIERAGENDA_WORKERS=1 \
    EIERAGENDA_THREADS=4 \
    EIERAGENDA_TIMEOUT=60

WORKDIR /app
COPY app/ /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
