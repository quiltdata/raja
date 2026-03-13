#!/bin/sh
set -e

AUTH_DISABLED_VALUE="${AUTH_DISABLED:-false}"
AUTH_DISABLED_VALUE="$(printf '%s' "$AUTH_DISABLED_VALUE" | tr '[:upper:]' '[:lower:]')"

JWKS_ENDPOINT_VALUE="${JWKS_ENDPOINT:-http://localhost:8001/.well-known/jwks.json}"
RAJA_ISSUER_VALUE="${RAJA_ISSUER:-http://localhost:8000}"
PUBLIC_PATH_PREFIXES_VALUE="${RAJEE_PUBLIC_PATH_PREFIXES:-}"
RALE_AUTHORIZER_URL_VALUE="${RALE_AUTHORIZER_URL:-}"
RALE_ROUTER_URL_VALUE="${RALE_ROUTER_URL:-}"

JWKS_SCHEME=$(printf '%s' "$JWKS_ENDPOINT_VALUE" | sed -n 's#^\(https\?\)://.*#\1#p')
JWKS_SCHEME="${JWKS_SCHEME:-http}"

JWKS_HOSTPORT="${JWKS_ENDPOINT_VALUE#*://}"
JWKS_HOSTPORT="${JWKS_HOSTPORT%%/*}"
if printf '%s' "$JWKS_HOSTPORT" | grep -q ":"; then
  JWKS_HOST="${JWKS_HOSTPORT%%:*}"
  JWKS_PORT="${JWKS_HOSTPORT##*:}"
else
  JWKS_HOST="$JWKS_HOSTPORT"
  if [ "$JWKS_SCHEME" = "https" ]; then
    JWKS_PORT="443"
  else
    JWKS_PORT="80"
  fi
fi

JWKS_TRANSPORT_SOCKET=""
if [ "$JWKS_SCHEME" = "https" ]; then
  JWKS_TRANSPORT_SOCKET=$(cat <<EOF
      transport_socket:
        name: envoy.transport_sockets.tls
        typed_config:
          "@type": type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.UpstreamTlsContext
          sni: ${JWKS_HOST}
EOF
)
fi

parse_url() {
  url="$1"
  default_port="$2"
  scheme=$(printf '%s' "$url" | sed -n 's#^\(https\?\)://.*#\1#p')
  hostport="${url#*://}"
  hostport="${hostport%%/*}"
  if printf '%s' "$hostport" | grep -q ":"; then
    host="${hostport%%:*}"
    port="${hostport##*:}"
  else
    host="$hostport"
    port="$default_port"
  fi
  printf '%s;%s;%s;%s\n' "$scheme" "$host" "$port" "$hostport"
}

RALE_ROUTING_ENABLED="false"
RALE_CLUSTERS=""
if [ -n "$RALE_AUTHORIZER_URL_VALUE" ] && [ -n "$RALE_ROUTER_URL_VALUE" ]; then
  auth_parts=$(parse_url "$RALE_AUTHORIZER_URL_VALUE" "443")
  router_parts=$(parse_url "$RALE_ROUTER_URL_VALUE" "443")

  RALE_AUTHORIZER_SCHEME=$(printf '%s' "$auth_parts" | cut -d';' -f1)
  RALE_AUTHORIZER_HOST=$(printf '%s' "$auth_parts" | cut -d';' -f2)
  RALE_AUTHORIZER_PORT=$(printf '%s' "$auth_parts" | cut -d';' -f3)
  RALE_AUTHORIZER_AUTHORITY=$(printf '%s' "$auth_parts" | cut -d';' -f4)

  RALE_ROUTER_SCHEME=$(printf '%s' "$router_parts" | cut -d';' -f1)
  RALE_ROUTER_HOST=$(printf '%s' "$router_parts" | cut -d';' -f2)
  RALE_ROUTER_PORT=$(printf '%s' "$router_parts" | cut -d';' -f3)
  RALE_ROUTER_AUTHORITY=$(printf '%s' "$router_parts" | cut -d';' -f4)

  export RALE_AUTHORIZER_AUTHORITY
  export RALE_ROUTER_AUTHORITY

  RALE_AUTHORIZER_TRANSPORT=""
  if [ "$RALE_AUTHORIZER_SCHEME" = "https" ]; then
    RALE_AUTHORIZER_TRANSPORT=$(cat <<EOF
      transport_socket:
        name: envoy.transport_sockets.tls
        typed_config:
          "@type": type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.UpstreamTlsContext
          sni: ${RALE_AUTHORIZER_HOST}
EOF
)
  fi

  RALE_ROUTER_TRANSPORT=""
  if [ "$RALE_ROUTER_SCHEME" = "https" ]; then
    RALE_ROUTER_TRANSPORT=$(cat <<EOF
      transport_socket:
        name: envoy.transport_sockets.tls
        typed_config:
          "@type": type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.UpstreamTlsContext
          sni: ${RALE_ROUTER_HOST}
EOF
)
  fi

  RALE_CLUSTERS=$(cat <<EOF
    - name: rale_authorizer_cluster
      type: LOGICAL_DNS
      dns_lookup_family: V4_ONLY
      connect_timeout: 5s
      lb_policy: ROUND_ROBIN
      typed_extension_protocol_options:
        envoy.extensions.upstreams.http.v3.HttpProtocolOptions:
          "@type": type.googleapis.com/envoy.extensions.upstreams.http.v3.HttpProtocolOptions
          explicit_http_config:
            http_protocol_options: {}
      load_assignment:
        cluster_name: rale_authorizer_cluster
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address:
                      address: ${RALE_AUTHORIZER_HOST}
                      port_value: ${RALE_AUTHORIZER_PORT}
${RALE_AUTHORIZER_TRANSPORT}
    - name: rale_router_cluster
      type: LOGICAL_DNS
      dns_lookup_family: V4_ONLY
      connect_timeout: 5s
      lb_policy: ROUND_ROBIN
      typed_extension_protocol_options:
        envoy.extensions.upstreams.http.v3.HttpProtocolOptions:
          "@type": type.googleapis.com/envoy.extensions.upstreams.http.v3.HttpProtocolOptions
          explicit_http_config:
            http_protocol_options: {}
      load_assignment:
        cluster_name: rale_router_cluster
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address:
                      address: ${RALE_ROUTER_HOST}
                      port_value: ${RALE_ROUTER_PORT}
${RALE_ROUTER_TRANSPORT}
EOF
)
  RALE_ROUTING_ENABLED="true"
fi

if [ "$AUTH_DISABLED_VALUE" = "1" ] || [ "$AUTH_DISABLED_VALUE" = "true" ] || [ "$AUTH_DISABLED_VALUE" = "yes" ] || [ "$AUTH_DISABLED_VALUE" = "on" ]; then
  AUTH_FILTER=""
  JWT_DEFAULT_RULE=""
else
  PUBLIC_PATH_RULES=""
  if [ -n "$PUBLIC_PATH_PREFIXES_VALUE" ]; then
    PUBLIC_PATH_RULES=$(printf '%s' "$PUBLIC_PATH_PREFIXES_VALUE" | tr ',' '\n' | awk 'NF { printf "                        - match:\n                            prefix: \"%s\"\n                          requires:\n                            allow_missing: {}\n", $0 }')
  fi
  if [ "$RALE_ROUTING_ENABLED" = "true" ]; then
    JWT_DEFAULT_RULE='                        - match:
                            prefix: "/"
                          requires:
                            allow_missing: {}'
  else
    JWT_DEFAULT_RULE='                        - match:
                            prefix: "/"
                          requires:
                            provider_name: raja_provider'
  fi

  AUTH_FILTER=$(cat <<'EOF'
                  - name: envoy.filters.http.jwt_authn
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.jwt_authn.v3.JwtAuthentication
                      providers:
                        raja_provider:
                          issuer: "__RAJA_ISSUER__"
                          audiences:
                            - "raja-s3-proxy"
                          from_headers:
                            - name: "x-raja-authorization"
                              value_prefix: "Bearer "
                            - name: "authorization"
                              value_prefix: "Bearer "
                          remote_jwks:
                            http_uri:
                              uri: "__JWKS_ENDPOINT__"
                              cluster: jwks_cluster
                              timeout: 5s
                            cache_duration: 600s
                          forward: true
                          forward_payload_header: "x-raja-jwt-payload"
                      rules:
__PUBLIC_PATH_RULES__
__JWT_DEFAULT_RULE__
                  - name: envoy.filters.http.lua
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.lua.v3.Lua
                      default_source_code:
                        inline_string: |
__AUTH_LUA__
EOF
)
fi

AUTH_LUA=$(sed 's/^/                          /' /etc/envoy/authorize.lua)

awk -v auth_filter="$AUTH_FILTER" \
    -v auth_lua="$AUTH_LUA" \
    -v public_path_rules="$PUBLIC_PATH_RULES" \
    -v jwks_host="$JWKS_HOST" \
    -v jwks_port="$JWKS_PORT" \
    -v jwks_transport="$JWKS_TRANSPORT_SOCKET" \
    -v rale_clusters="$RALE_CLUSTERS" \
    -v jwks_endpoint="$JWKS_ENDPOINT_VALUE" \
    -v raja_issuer="$RAJA_ISSUER_VALUE" \
    -v jwt_default_rule="$JWT_DEFAULT_RULE" \
    '{
      gsub(/__AUTH_FILTER__/, auth_filter)
      gsub(/__PUBLIC_PATH_RULES__/, public_path_rules)
      gsub(/__JWT_DEFAULT_RULE__/, jwt_default_rule)
      gsub(/__AUTH_LUA__/, auth_lua)
      gsub(/__JWKS_HOST__/, jwks_host)
      gsub(/__JWKS_PORT__/, jwks_port)
      gsub(/__JWKS_TRANSPORT_SOCKET__/, jwks_transport)
      gsub(/__RALE_CLUSTERS__/, rale_clusters)
      gsub(/__JWKS_ENDPOINT__/, jwks_endpoint)
      gsub(/__RAJA_ISSUER__/, raja_issuer)
    }1' /etc/envoy/envoy.yaml.tmpl > /tmp/envoy.yaml

if [ "${ENVOY_VALIDATE:-}" = "true" ] || [ "${ENVOY_VALIDATE:-}" = "1" ]; then
  if ! envoy --mode validate -c /tmp/envoy.yaml; then
    echo "Envoy config validation failed; rendered config:" >&2
    cat /tmp/envoy.yaml >&2
    exit 1
  fi
  exit 0
fi

exec envoy -c /tmp/envoy.yaml --log-level "${ENVOY_LOG_LEVEL:-info}"
