FROM envoyproxy/envoy:v1.28-latest

# Install curl for health checks
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY infra/raja_poc/assets/envoy/envoy.yaml.tmpl /etc/envoy/envoy.yaml.tmpl
COPY infra/raja_poc/assets/envoy/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod +x /usr/local/bin/entrypoint.sh

ENV AUTH_DISABLED=true

CMD ["/usr/local/bin/entrypoint.sh"]
