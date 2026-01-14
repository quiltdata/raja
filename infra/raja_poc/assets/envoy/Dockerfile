FROM envoyproxy/envoy:v1.28-latest

COPY infra/raja_poc/assets/envoy/envoy.yaml /etc/envoy/envoy.yaml

CMD ["envoy", "-c", "/etc/envoy/envoy.yaml", "--log-level", "info"]
