FROM node:24-bookworm-slim AS studio
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ARG BLENDER_VERSION=4.5.3
ARG BLENDER_SHA256=975c58fcb244273838534bba771e64ad87739216b0f9b39a888531a49a72d845
RUN apt-get update && apt-get install --no-install-recommends -y \
    ca-certificates curl xz-utils libx11-6 libxi6 libxxf86vm1 libxfixes3 \
    libxrender1 libsm6 libice6 libegl1 libgl1 libgomp1 libxkbcommon0 \
    && rm -rf /var/lib/apt/lists/*
RUN curl --fail --show-error --location --retry 3 \
    "https://download.blender.org/release/Blender4.5/blender-${BLENDER_VERSION}-linux-x64.tar.xz" \
    -o /tmp/blender.tar.xz \
    && echo "${BLENDER_SHA256}  /tmp/blender.tar.xz" | sha256sum --check - \
    && mkdir /opt/blender \
    && tar -xJf /tmp/blender.tar.xz --strip-components=1 -C /opt/blender \
    && rm /tmp/blender.tar.xz
WORKDIR /opt/locusforge
COPY requirements.lock pyproject.toml README.md LICENSE ./
RUN pip install --no-cache-dir -r requirements.lock
COPY src/ ./src/
COPY --from=studio /build/src/locusforge/static/ ./src/locusforge/static/
RUN pip install --no-cache-dir --no-deps . \
    && groupadd --gid 10001 locus \
    && useradd --uid 10001 --gid locus --no-create-home locus \
    && mkdir -p /data && chown locus:locus /data
ENV LOCUS_DATA_DIR=/data \
    LOCUS_BLENDER=/opt/blender/blender \
    LOCUS_HOST=0.0.0.0 \
    LOCUS_PORT=8765 \
    LOCUS_RENDER_THREADS=4 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3)"
ENTRYPOINT ["locusforge"]
CMD ["serve"]
