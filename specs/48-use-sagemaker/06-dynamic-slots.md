# Remove Hardcoded owner/users/guests Slot Terminology

## Goals

1. **Primary:** Make DataZone projects fully data-driven from `seed-config.yaml`.
   No slot name (`owner`, `users`, `guests`) should appear as a literal string
   anywhere outside that file.

2. **Critical bug fix (discovered along the way):** Delete `project_id_for_scopes()`
   — it is inverted shadow auth and must not exist.

---

## Critical Bug: `project_id_for_scopes()`

```python
# src/raja/datazone/service.py
def project_id_for_scopes(scopes: list[str], config: DataZoneConfig) -> str:
    has_wildcard = any("*" in s for s in scopes)
    if has_wildcard:
        return config.owner_project_id   # scopes → project
    has_write = any(...)
    if has_write:
        return config.users_project_id   # scopes → project
    return config.guests_project_id      # scopes → project
```

This function runs authorization **backwards**. It takes a principal's scopes and
infers which DataZone project they belong to. That is a runtime policy evaluator
hidden inside a utility function.

The correct flow is:

```
principal → DataZone membership lookup → project → access decision
```

DataZone project membership IS the authorization. The function inverts this by
deriving project from scopes, creating a second, hidden policy engine that silently
overrides the actual DataZone membership. **Delete it.**

---

## Affected Files

| File | What is hardcoded |
|------|--------------------|
| `scripts/seed_config.py` | `slot_map = {"owner": ..., "users": ..., "guests": ...}` in `project_id_map()` |
| `src/raja/datazone/service.py` | `DataZoneConfig` fields `owner_project_id/users_project_id/guests_project_id`; `project_id_for_scopes()` |
| `scripts/sagemaker_gaps.py` | `PROJECT_OUTPUT_KEYS`, `ENVIRONMENT_SPECS`, `env_updates` dict literals |
| `scripts/seed_users.py` | `_DATAZONE_ENV_MAP`, `_get_raja_guests()` |
| `infra/terraform/main.tf` | Three named resources `aws_datazone_project.owner/users/guests`; output names |
| `tests/unit/test_datazone_service.py` | Constants `_OWNER_PROJECT`, `_USERS_PROJECT`, `_GUESTS_PROJECT`; `project_id_for_scopes()` tests |
| `tests/integration/test_datazone_service.py` | `_DATAZONE_ENV_MAP`, list of `owner/users/guests_project_id` |
| `.env` / Lambda environment | `DATAZONE_OWNER_PROJECT_ID`, `DATAZONE_USERS_PROJECT_ID`, `DATAZONE_GUESTS_PROJECT_ID` |

---

## Design

### 1. `seed-config.yaml` is the source of truth — no changes needed

The `slot` field on each project is an opaque string label. The implementation must
read it, not redefine it.

### 2. Replace `DataZoneConfig` fixed fields with a slot map

**Before:**
```python
@dataclass(frozen=True)
class DataZoneConfig:
    domain_id: str
    owner_project_id: str = ""
    users_project_id: str = ""
    guests_project_id: str = ""
    owner_project_label: str = "Project A"
    users_project_label: str = "Project B"
    guests_project_label: str = "Project C"
    owner_environment_id: str = ""
    users_environment_id: str = ""
    guests_environment_id: str = ""
```

**After:**
```python
@dataclass(frozen=True)
class SlotConfig:
    project_id: str = ""
    project_label: str = ""
    environment_id: str = ""

@dataclass(frozen=True)
class DataZoneConfig:
    domain_id: str
    slots: dict[str, SlotConfig] = field(default_factory=dict)
    asset_type_name: str = "QuiltPackage"
    asset_type_revision: str = "1"

    def slot(self, name: str) -> SlotConfig:
        return self.slots.get(name, SlotConfig())
```

`from_env()` reads a single `DATAZONE_SLOTS` JSON env var instead of nine separate
ones:

```python
@classmethod
def from_env(cls) -> DataZoneConfig:
    slots_json = os.environ.get("DATAZONE_SLOTS", "{}")
    raw: dict[str, dict[str, str]] = json.loads(slots_json)
    slots = {k: SlotConfig(**v) for k, v in raw.items()}
    return cls(domain_id=os.environ["DATAZONE_DOMAIN_ID"], slots=slots)
```

### 3. Delete `project_id_for_scopes()` and its call sites

Remove the function entirely. Any call site that was using it to decide which
DataZone project to place a principal in must instead look up the principal's
**actual** DataZone project membership.

The unit tests for this function (`test_project_id_wildcard_scope_returns_owner`,
etc.) are deleted with it.

### 4. `seed_config.py` — derive `project_id_map()` from slot names

**Before:**
```python
def project_id_map(self, datazone_config: DataZoneConfig) -> dict[str, str]:
    slot_map = {
        "owner": datazone_config.owner_project_id,
        "users": datazone_config.users_project_id,
        "guests": datazone_config.guests_project_id,
    }
    return {project.key: slot_map.get(project.slot, "") for project in self.projects}
```

**After:**
```python
def project_id_map(self, datazone_config: DataZoneConfig) -> dict[str, str]:
    return {
        project.key: datazone_config.slot(project.slot).project_id
        for project in self.projects
    }
```

### 5. `sagemaker_gaps.py` — generate maps from seed config

Replace `PROJECT_OUTPUT_KEYS` and the `env_updates` literal with derived values:

```python
def _build_datazone_slots_json(
    seed_config: SeedConfig,
    project_ids: dict[str, str],      # slot → project_id
    environment_ids: dict[str, str],  # slot → environment_id
) -> str:
    slots = {
        p.slot: {
            "project_id": project_ids.get(p.slot, ""),
            "project_label": p.display_name,
            "environment_id": environment_ids.get(p.slot, ""),
        }
        for p in seed_config.projects
    }
    return json.dumps(slots)

env_updates = {
    "DATAZONE_DOMAIN_ID": domain_id,
    "DATAZONE_SLOTS": _build_datazone_slots_json(seed_config, project_ids, environment_ids),
}
```

### 6. `seed_users.py` — remove slot-name literals

Replace `_DATAZONE_ENV_MAP` with `DataZoneConfig.from_env()`. Remove
`_get_raja_guests()` — the `guests` slot name must not appear in code.

### 7. Terraform — `for_each` over projects from seed config

Replace three named `aws_datazone_project` resources with a `for_each`:

```hcl
variable "datazone_projects" {
  description = "Map of slot name to project display name, matching seed-config.yaml"
  type        = map(string)
  default = {
    owner  = "Alpha"
    users  = "Bio"
    guests = "Compute"
  }
}

resource "aws_datazone_project" "slots" {
  for_each          = var.datazone_projects
  domain_identifier = aws_datazone_domain.raja.id
  name              = each.value
}

output "datazone_slot_project_ids" {
  value = { for k, v in aws_datazone_project.slots : k => v.id }
}
```

Lambda env:

```hcl
DATAZONE_DOMAIN_ID = aws_datazone_domain.raja.id
DATAZONE_SLOTS     = jsonencode({
  for slot, proj in aws_datazone_project.slots : slot => {
    project_id    = proj.id
    project_label = proj.name
    environment_id = lookup(local.environment_ids, slot, "")
  }
})
```

> **Migration:** Use `terraform state mv` to avoid recreating existing projects:
>
> ```sh
> terraform state mv 'aws_datazone_project.owner' 'aws_datazone_project.slots["owner"]'
> terraform state mv 'aws_datazone_project.users' 'aws_datazone_project.slots["users"]'
> terraform state mv 'aws_datazone_project.guests' 'aws_datazone_project.slots["guests"]'
> ```

---

## Implementation Order

1. **Delete `project_id_for_scopes()`** and its tests — remove the bug first so
   nothing new is built on top of it.
2. **`DataZoneConfig` + `SlotConfig`** — replace fixed fields; update `from_env()`.
3. **`seed_config.py`** — simplify `project_id_map()`.
4. **`sagemaker_gaps.py`** — replace literal maps with derived values.
5. **`seed_users.py`** — remove `_DATAZONE_ENV_MAP` and `_get_raja_guests()`.
6. **Terraform** — `for_each` + `terraform state mv`.
7. **Integration tests** — derive slot names from `SEED_CONFIG`; remove literal
   `owner/users/guests` references.
