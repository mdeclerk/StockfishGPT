# syntax=docker/dockerfile:1

FROM node:24-bookworm-slim AS widget-build
WORKDIR /build/widget
COPY widget/package.json widget/package-lock.json ./
RUN npm ci
COPY widget/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:latest AS uv

FROM python:3.14-slim-bookworm AS application-build
COPY --from=uv /uv /bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/stockfish-gpt \
    UV_PYTHON_PREFERENCE=only-system

WORKDIR /build
COPY pyproject.toml uv.lock LICENSE ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev --no-editable

ARG TARGETARCH

FROM --platform=$BUILDPLATFORM debian:bookworm-slim AS stockfish-amd64
ENV STOCKFISH_BINARY="stockfish-ubuntu-x86-64"
ADD --checksum=sha256:5c6f38b02a4da5f3ffe763f27da6c3e743eebefd92b50cb3661623b96696adff \
    https://github.com/official-stockfish/Stockfish/releases/download/sf_18/stockfish-ubuntu-x86-64.tar \
    /tmp/stockfish.tar

FROM --platform=$BUILDPLATFORM debian:bookworm-slim AS stockfish-arm64
ENV STOCKFISH_BINARY="stockfish-android-armv8"
ADD --checksum=sha256:e2eca54b0e3189ec7de338133c2b34fa8f5cdec3d2473519b414a5cb6815e768 \
    https://github.com/official-stockfish/Stockfish/releases/download/sf_18/stockfish-android-armv8.tar \
    /tmp/stockfish.tar

FROM stockfish-${TARGETARCH} AS stockfish-download
RUN mkdir --parents /out/usr/local/bin /out/usr/share/doc/stockfish \
    && tar --extract --file /tmp/stockfish.tar --directory /tmp \
        "stockfish/${STOCKFISH_BINARY}" stockfish/Copying.txt \
    && install --mode 0755 \
        "/tmp/stockfish/${STOCKFISH_BINARY}" \
        /out/usr/local/bin/stockfish \
    && install --mode 0644 \
        /tmp/stockfish/Copying.txt \
        /out/usr/share/doc/stockfish/COPYING \
    && printf '%s\n' "sf_18" \
        > /out/usr/share/doc/stockfish/VERSION \
    && printf '%s\n' \
        "https://github.com/official-stockfish/Stockfish/releases/tag/sf_18" \
        > /out/usr/share/doc/stockfish/SOURCE

FROM python:3.14-slim-bookworm AS runtime
LABEL org.opencontainers.image.title="StockfishGPT" \
      org.opencontainers.image.description="Interactive engine-grounded chess coach for ChatGPT" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.licenses="GPL-3.0-or-later"

RUN groupadd --gid 10001 stockfish-gpt \
    && useradd --uid 10001 --gid stockfish-gpt --no-create-home stockfish-gpt

WORKDIR /app
COPY --from=application-build /opt/stockfish-gpt/ /opt/stockfish-gpt/
COPY --from=widget-build /build/widget/dist/ /app/widget/
COPY --from=stockfish-download /out/ /
COPY LICENSE THIRD_PARTY_NOTICES.md ./

ENV PATH="/opt/stockfish-gpt/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
CMD ["mcp-app", "--wdir", "/app/widget", "--host", "0.0.0.0"]
