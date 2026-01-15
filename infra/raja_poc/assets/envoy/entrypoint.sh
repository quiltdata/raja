#!/bin/sh
set -e

AUTH_DISABLED_VALUE="${AUTH_DISABLED:-true}"
AUTH_DISABLED_VALUE="$(printf '%s' "$AUTH_DISABLED_VALUE" | tr '[:upper:]' '[:lower:]')"

if [ "$AUTH_DISABLED_VALUE" = "1" ] || [ "$AUTH_DISABLED_VALUE" = "true" ] || [ "$AUTH_DISABLED_VALUE" = "yes" ] || [ "$AUTH_DISABLED_VALUE" = "on" ]; then
  AUTH_FILTER=""
else
  AUTH_FILTER=$(cat <<'EOF'
                  - name: envoy.filters.http.fault
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.fault.v3.HTTPFault
                      abort:
                        http_status: 403
                        percentage:
                          numerator: 100
                          denominator: HUNDRED
                      response_headers_to_add:
                        - header:
                            key: "x-raja-auth"
                            value: "required"
EOF
)
fi

awk -v auth_filter="$AUTH_FILTER" '{gsub(/__AUTH_FILTER__/, auth_filter)}1' /etc/envoy/envoy.yaml.tmpl \
  > /tmp/envoy.yaml

exec envoy -c /tmp/envoy.yaml --log-level "${ENVOY_LOG_LEVEL:-info}"
