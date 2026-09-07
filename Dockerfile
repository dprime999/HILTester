FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

ARG RUNNER_VERSION=2.337.0
ARG PICO_SDK_VERSION=2.3.0
ARG PICOTOOL_VERSION=2.3.0

# ------------------------------------------------------------
# Base development + HIL dependencies
# ------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    binutils-arm-none-eabi \
    build-essential \
    ca-certificates \
    cmake \
    curl \
    gcc-arm-none-eabi \
    git \
    jq \
    libnewlib-arm-none-eabi \
    libstdc++-arm-none-eabi-newlib \
    libusb-1.0-0 \
    libusb-1.0-0-dev \
    make \
    ninja-build \
    openocd \
    pkg-config \
    sudo \
    tar \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------------------
# Build + install picotool 2.3.0
#
# Ubuntu 24.04 does not provide the current picotool package,
# so build it once into this Docker image.
# ------------------------------------------------------------
RUN git clone \
        --branch "${PICO_SDK_VERSION}" \
        --depth 1 \
        https://github.com/raspberrypi/pico-sdk.git \
        /opt/pico-sdk \
    && git clone \
        --branch "${PICOTOOL_VERSION}" \
        --depth 1 \
        https://github.com/raspberrypi/picotool.git \
        /tmp/picotool \
    && cmake \
        -S /tmp/picotool \
        -B /tmp/picotool-build \
        -G Ninja \
        -DPICO_SDK_PATH=/opt/pico-sdk \
        -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /tmp/picotool-build \
    && cmake --install /tmp/picotool-build \
    && rm -rf /tmp/picotool /tmp/picotool-build

# ------------------------------------------------------------
# Create non-root GitHub Actions runner user
# ------------------------------------------------------------
RUN useradd --create-home --shell /bin/bash runner \
    && echo "runner ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/runner \
    && chmod 0440 /etc/sudoers.d/runner

# ------------------------------------------------------------
# Download correct GitHub Actions runner for this architecture.
#
# Pi 5 / 64-bit Raspberry Pi OS = arm64
# Typical desktop/server Linux = amd64 -> x64 package
# ------------------------------------------------------------
RUN mkdir -p /home/runner/actions-runner \
    && ARCH="$(dpkg --print-architecture)" \
    && case "${ARCH}" in \
        arm64) RUNNER_ARCH="arm64" ;; \
        amd64) RUNNER_ARCH="x64" ;; \
        *) echo "Unsupported architecture: ${ARCH}" && exit 1 ;; \
    esac \
    && curl -fL \
        "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz" \
        -o /tmp/actions-runner.tar.gz \
    && tar -xzf /tmp/actions-runner.tar.gz \
        -C /home/runner/actions-runner \
    && rm /tmp/actions-runner.tar.gz \
    && chown -R runner:runner /home/runner/actions-runner

# ------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------
COPY --chown=runner:runner entrypoint.sh \
    /home/runner/actions-runner/entrypoint.sh

RUN chmod +x /home/runner/actions-runner/entrypoint.sh

USER runner
WORKDIR /home/runner/actions-runner

ENTRYPOINT ["./entrypoint.sh"]