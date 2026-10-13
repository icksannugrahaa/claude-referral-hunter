FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md LICENSE requirements-x.txt ./
COPY src ./src
ARG EXTRAS=telegram
ARG WITH_X=1
RUN pip install --no-cache-dir ".[${EXTRAS}]" \
 && if [ "${WITH_X:-1}" = "1" ]; then pip install --no-cache-dir -r requirements-x.txt; fi
ENV CRH_STATE_DIR=/data PYTHONUNBUFFERED=1
VOLUME /data
ENTRYPOINT ["claude-referral-hunter"]
CMD ["run"]
