FROM python:3.13-slim AS builder
COPY . /src
RUN pip install /src && ftl-gate-builder --all-modules -o /tmp/gate.pyz

FROM python:3.13-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-server \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/sshd \
    && ssh-keygen -A
RUN useradd -m -s /bin/bash ftl2
COPY --from=builder /tmp/gate.pyz /usr/local/lib/ftl2/gate.pyz
COPY containers/ftl2/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 22
ENTRYPOINT ["/entrypoint.sh"]
