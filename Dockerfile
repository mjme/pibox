# pibox: the pi coding agent in an isolated container.
# Ollama is NOT in this image. It runs natively on the Mac (Metal GPU). The box has no
# network access of its own: it reaches Ollama only through pibox-proxy.py, which runs
# from this same image as a separate container. See the `pibox` script for the flags.
FROM node:22-bookworm-slim

# Tools the agent commonly reaches for. Add your own here (the box runs without root
# and with a read-only system, so the agent can't install things at runtime).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      bash ca-certificates curl git jq less procps \
      ripgrep fd-find tree unzip zip make \
      python3 python3-pip python3-venv \
      ncurses-term locales \
 && ln -s /usr/bin/fdfind /usr/local/bin/fd \
 && sed -i 's/# en_US.UTF-8/en_US.UTF-8/' /etc/locale.gen && locale-gen \
 && rm -rf /var/lib/apt/lists/*

# Declared here, not at the top, so a new pi version only redoes this step.
ARG PI_VERSION=latest
RUN npm install -g --ignore-scripts "@earendil-works/pi-coding-agent@${PI_VERSION}" \
 && npm cache clean --force

COPY --chmod=755 pibox-init.py pibox-proxy.py pibox-entry /usr/local/bin/

# Pre-create pi's config dir owned by the unprivileged `node` user (uid 1000) so
# the named volume mounted over it inherits the right ownership.
RUN mkdir -p /home/node/.pi/agent && chown -R node:node /home/node/.pi

# The box has no internet, so stop pi from trying: no update checks, model catalog
# refreshes, package installs or install telemetry.
ENV LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8 \
    TERM=xterm-256color \
    COLORTERM=truecolor \
    PI_OFFLINE=1 \
    PI_SKIP_VERSION_CHECK=1 \
    PI_TELEMETRY=0 \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    PATH=/home/node/.local/bin:$PATH

USER node
WORKDIR /home/node
ENTRYPOINT ["/usr/local/bin/pibox-entry"]
