# The digital half of ohmwork, as one container.
#
# WHY A CONTAINER AND NOT A SERVERLESS FUNCTION. The thing that verifies an
# answer here is Logisim Evolution, which is Java. It has to actually run on
# the server -- a platform with no JVM, or a 60-second function limit, cannot
# host this at all. That single fact decides the whole deployment.
#
# Analog stays on the command line: LTspice is a Windows GUI application, and
# ngspice is not a substitute because it cannot read LTspice's device
# libraries. A hosted analog answer could only ever be unverified.

# ----------------------------------------------------------- the frontend
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY web/ ./
RUN npm run build


# ------------------------------------------------------------ the runtime
FROM python:3.12-slim

# Java 21, copied from the official image rather than from Debian, whose
# stable JRE is too old for Logisim Evolution 4.x.
COPY --from=eclipse-temurin:21-jre /opt/java/openjdk /opt/java/openjdk
ENV JAVA_HOME=/opt/java/openjdk
ENV PATH="/opt/java/openjdk/bin:${PATH}"

# xvfb: Logisim is a desktop application being driven in --tty mode, and
# giving it a virtual display is cheaper than gambling on every AWT call
# being headless-safe. Measured cost: a few MB and no measurable latency.
#
# xauth IS REQUIRED and is easy to miss: Debian's xvfb only RECOMMENDS it, and
# --no-install-recommends is used here, so it does not arrive. Without it
# `xvfb-run` exits immediately with "xauth command not found" -- which is what
# the first real build of this image did, in 150 milliseconds, with the API
# never coming up at all. Found in CI, which is the entire reason that
# workflow runs before a deploy does.
RUN apt-get update \
 && apt-get install -y --no-install-recommends xvfb xauth curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# PINNED. This is the evaluator every published number in this project was
# measured against; a floating "latest" would silently change what "verified"
# means between deploys.
ARG LOGISIM_VERSION=4.1.0
RUN curl -fsSL -o /opt/logisim-evolution.jar \
    "https://github.com/logisim-evolution/logisim-evolution/releases/download/v${LOGISIM_VERSION}/logisim-evolution-${LOGISIM_VERSION}-all.jar"
ENV OHMWORK_LOGISIM=/opt/logisim-evolution.jar

WORKDIR /app
COPY pyproject.toml ./
COPY ohmwork ./ohmwork
RUN pip install --no-cache-dir ".[web,llm]"

COPY --from=web /web/dist ./web/dist

# HOME must be writable: Logisim writes a preferences file on first run, and
# a read-only home turns that into a startup failure with an unrelated-looking
# message.
# OHMWORK_STATIC is explicit rather than inferred. Without it the page is
# found only because /app happens to be on sys.path ahead of site-packages,
# so `__file__/../../web/dist` happens to resolve here -- a coincidence
# between the working directory and the install layout that nobody chose.
# Stating the path costs nothing and does not depend on it.
ENV HOME=/tmp \
    OHMWORK_STATIC=/app/web/dist \
    PORT=7860 \
    OHMWORK_SECURE_COOKIES=1 \
    OHMWORK_LLM=pool \
    PYTHONUNBUFFERED=1

EXPOSE 7860

# A quick liveness probe. It says nothing about configuration on purpose --
# it is the one route reachable without the password.
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:7860/api/health').read()"

COPY deploy/entrypoint.sh /usr/local/bin/ohmwork-entrypoint
RUN chmod +x /usr/local/bin/ohmwork-entrypoint

# Not `xvfb-run`: see deploy/entrypoint.sh for the measurement that changed
# this. In short, the server must be PID 1 so that its output is the
# container's log and `docker stop` reaches it.
CMD ["/usr/local/bin/ohmwork-entrypoint"]
