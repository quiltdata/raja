# syntax=docker/dockerfile:1
# check=skip=SecretsUsedInArgOrEnv

FROM envoyproxy/envoy:v1.28-latest

# Install curl for health checks and Lua JSON support for auth filter
RUN apt-get update \
    && apt-get install -y curl lua-cjson \
    && rm -rf /var/lib/apt/lists/*

COPY infra/raja_poc/assets/envoy/envoy.yaml.tmpl /etc/envoy/envoy.yaml.tmpl
COPY infra/raja_poc/assets/envoy/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY infra/raja_poc/assets/envoy/authorize_lib.lua /usr/local/share/lua/5.1/authorize_lib.lua
COPY infra/raja_poc/assets/envoy/authorize.lua /etc/envoy/authorize.lua

RUN chmod +x /usr/local/bin/entrypoint.sh

# AUTH_DISABLED is a configuration flag (true/false), not sensitive data
ENV AUTH_DISABLED=false

CMD ["/usr/local/bin/entrypoint.sh"]
