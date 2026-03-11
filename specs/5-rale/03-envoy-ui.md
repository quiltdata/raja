# Envoy Admin UI: Current State and Public Exposure

## What It Is

Envoy ships a built-in HTML admin interface that serves live operational data:

- `/` — dashboard with links to all admin endpoints
- `/stats` — all Envoy counters and gauges (requests, errors, latency)
- `/clusters` — upstream cluster health and connection state
- `/config_dump` — full rendered Envoy config (useful for debugging Lua filters)
- `/logging` — change log level at runtime without restart
- `/ready` — readiness probe (used by ALB health checks)
- `/server_info` — version, uptime, state

It is a real browser-accessible web UI, not just a JSON API.

## Current State

Envoy binds the admin interface to `0.0.0.0:9901` ([envoy.yaml.tmpl](../../infra/raja_poc/assets/envoy/envoy.yaml.tmpl#L78)):

```yaml
admin:
  address:
    socket_address:
      address: 0.0.0.0
      port_value: 9901
```

### In AWS (Terraform deployment)

The ECS service runs in **private subnets**. Port 9901 is reachable only from the ALB security group, which uses it exclusively for health checks:

```
Internet → ALB (public subnet) → port 10000 → Envoy (private subnet)
                                → port 9901  → health check only (not a listener)
```

The ALB has **no listener** on port 9901, so there is no public path to the admin UI.

ECS Exec is **not enabled** on the service, so there is no in-band shell access either.

### Locally (docker-compose)

Port 9901 is forwarded directly to localhost ([docker-compose.local.yml](../../infra/raja_poc/assets/envoy/docker-compose.local.yml#L24)):

```yaml
ports:
  - "9901:9901"
```

Access it at: `http://localhost:9901/`

```bash
docker compose -f infra/raja_poc/assets/envoy/docker-compose.local.yml up
# then open http://localhost:9901/
```

## How to Expose It in AWS

### Option A: ECS Exec (No Infrastructure Changes — Recommended for Dev)

Enable `execute_command` on the ECS service in [main.tf](../../infra/terraform/main.tf):

```hcl
resource "aws_ecs_service" "rajee" {
  # ... existing config ...
  enable_execute_command = true
}
```

The task role also needs SSM permissions:

```hcl
resource "aws_iam_role_policy" "rajee_task_permissions" {
  # add to existing statements:
  {
    Effect = "Allow"
    Action = [
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel"
    ]
    Resource = ["*"]
  }
}
```

After `terraform apply`, use the AWS CLI to open a shell:

```bash
TASK_ID=$(aws ecs list-tasks \
  --cluster <stack-name>-rajee-cluster \
  --service-name <stack-name>-rajee-service \
  --query 'taskArns[0]' --output text | awk -F/ '{print $NF}')

aws ecs execute-command \
  --cluster <stack-name>-rajee-cluster \
  --task $TASK_ID \
  --container EnvoyProxy \
  --interactive \
  --command "/bin/sh"

# Inside the container:
curl http://localhost:9901/
curl http://localhost:9901/stats
```

Or dump stats directly without an interactive shell:

```bash
aws ecs execute-command \
  --cluster <stack-name>-rajee-cluster \
  --task $TASK_ID \
  --container EnvoyProxy \
  --interactive \
  --command "curl -s http://localhost:9901/stats"
```

**Security:** No public exposure. Access controlled by IAM (`ecs:ExecuteCommand`).

---

### Option B: Second ALB Listener on Port 9901 (IP-Restricted)

Add a second target group, security group rule, and ALB listener in [main.tf](../../infra/terraform/main.tf).

**New variable:**

```hcl
variable "admin_allowed_cidrs" {
  description = "CIDRs allowed to access the Envoy admin UI"
  type        = list(string)
  default     = []  # empty = admin UI not exposed
}
```

**Security group rule** (add to `aws_security_group.rajee_alb`):

```hcl
dynamic "ingress" {
  for_each = length(var.admin_allowed_cidrs) > 0 ? [1] : []
  content {
    from_port   = 9901
    to_port     = 9901
    protocol    = "tcp"
    cidr_blocks = var.admin_allowed_cidrs
  }
}
```

**Allow ALB to reach Envoy port 9901** (add to `aws_security_group.rajee_service`):

```hcl
ingress {
  from_port       = 9901
  to_port         = 9901
  protocol        = "tcp"
  security_groups = [aws_security_group.rajee_alb.id]
}
```

**Target group:**

```hcl
resource "aws_lb_target_group" "rajee_admin" {
  count       = length(var.admin_allowed_cidrs) > 0 ? 1 : 0
  name        = substr("${replace(var.stack_name, "_", "-")}-rajee-admin", 0, 32)
  port        = 9901
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.rajee.id

  health_check {
    path     = "/ready"
    port     = "9901"
    protocol = "HTTP"
  }
}
```

**ALB listener:**

```hcl
resource "aws_lb_listener" "rajee_admin" {
  count             = length(var.admin_allowed_cidrs) > 0 ? 1 : 0
  load_balancer_arn = aws_lb.rajee.arn
  port              = 9901
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.rajee_admin[0].arn
  }
}
```

**ECS service update** — add the admin target group to the service:

```hcl
resource "aws_ecs_service" "rajee" {
  # ... existing config ...

  dynamic "load_balancer" {
    for_each = length(var.admin_allowed_cidrs) > 0 ? [1] : []
    content {
      target_group_arn = aws_lb_target_group.rajee_admin[0].arn
      container_name   = "EnvoyProxy"
      container_port   = 9901
    }
  }
}
```

**Output:**

```hcl
output "rajee_admin_url" {
  description = "Envoy admin UI (only set when admin_allowed_cidrs is non-empty)"
  value       = length(var.admin_allowed_cidrs) > 0 ? "http://${aws_lb.rajee.dns_name}:9901/" : ""
}
```

**Usage:**

```bash
terraform apply -var='admin_allowed_cidrs=["203.0.113.0/24"]'
# then open http://<alb-dns>:9901/
```

**Security:** Restricted to specified CIDRs. No TLS on port 9901 (Envoy admin does not support TLS natively). Do not use for production without a TLS termination layer in front.

---

## Recommendation

| Use case | Option |
|---|---|
| Local development / debugging | docker-compose (already works) |
| One-off AWS debugging | Option A (ECS Exec) |
| Persistent team visibility | Option B (ALB listener, IP-restricted) |

Option A requires a single `terraform apply` to enable ECS Exec and is the safest path — admin traffic never leaves AWS and access is gated by IAM. Option B is appropriate if you need a browser-accessible dashboard reachable from a fixed office or VPN CIDR.
