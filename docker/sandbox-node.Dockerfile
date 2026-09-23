# Administrator-built mixed Python/Node examples. Record resolved image IDs in
# the release manifest; runtime packages install into workspace dependency mounts.
ARG PYTHON_RUNTIME_IMAGE=enterprise-agent-sandbox:1
ARG NODE_RUNTIME_IMAGE=enterprise-agent-sandbox-node:1
FROM ${NODE_RUNTIME_IMAGE} AS node_runtime
FROM ${PYTHON_RUNTIME_IMAGE}
USER 0:0
COPY --from=node_runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node_runtime /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
USER 10001:10001
