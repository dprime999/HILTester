FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

ARG RUNNER_VERSION=2.337.0

# ------------------------------------------------------------
# Base development + HIL dependencies
#
# STM32 toolchain stays explicit here so the STM32 build/flash
# environment remains guaranteed regardless of pico_setup.sh.
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
    python3 \
    python3-pip \
    sudo \
    tar \
    usbutils \
    wget \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------------------
# Create non-root GitHub Actions runner user
# ------------------------------------------------------------
RUN useradd --create-home --shell /bin/bash runner \
    && echo "runner ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/runner \
    && chmod 0440 /etc/sudoers.d/runner

# ------------------------------------------------------------
# Raspberry Pi Pico setup
#
# Use Raspberry Pi's setup script instead of manually cloning
# and assembling the Pico SDK + picotool stack ourselves.
#
# Run as the runner user so user-local Pico files live under
# /home/runner, similar to your old working host setup.
# ------------------------------------------------------------
USER runner
WORKDIR /home/runner

RUN wget \
        https://raw.githubusercontent.com/raspberrypi/pico-setup/master/pico_setup.sh \
        -O /home/runner/pico_setup.sh \
    && chmod +x /home/runner/pico_setup.sh \
    && /home/runner/pico_setup.sh

# ------------------------------------------------------------
# Pico SDK location created by pico_setup.sh
# ------------------------------------------------------------
ENV PICO_SDK_PATH=/home/runner/pico/pico-sdk

# ------------------------------------------------------------
# Download correct GitHub Actions runner for this architecture.
#
# Raspberry Pi 5 / 64-bit Raspberry Pi OS = arm64
# Typical desktop/server Linux = amd64 -> x64 package
# ------------------------------------------------------------
USER root

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