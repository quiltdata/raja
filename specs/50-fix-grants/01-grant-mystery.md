# Grant Mystery: Kevin Gets DENY Despite Proper Memberships

## The Initial Error

Kevin ran `uv run rale` on his machine and selected `demo/package-grant`. The CLI
displayed his principal correctly as an IAM ARN and then returned:

```text
Error: DENY - no DataZone package grant permits this principal + package
```

Principal shown: `arn:aws:iam::712023778557:user/kevin-staging`

The Admin UI confirmed both `ernest-staging` and `kevin-staging` appear as members
of **Owner** and **Users** projects. The subscriptions panel showed two ACCEPTED
grants for `demo/package-grant` — one for Guests, one for Users.

---

## Mystery 1: Unhelpful DENY message

The CLI error was identical regardless of *why* the authorizer denied:

- "principal not in any DataZone project" (membership lookup failed)
- "principal in a project, but that project has no subscription grant"

Both paths returned HTTP 403 and the CLI printed the same string, making it
impossible to distinguish them without inspecting the Lambda logs.

**Fix applied:** The CLI now reads the response body on 403 and surfaces the
`error` field from the authorizer, which does distinguish the two cases
(`"principal project not found"` vs no error key but package info present).

---

## Mystery 2: Integration tests couldn't be made to fail

To validate the new `test_seed_users` and `test_package_grant` integration tests,
we tried removing `ernest-staging` from all three projects one at a time and
re-running the suite after each removal. The tests kept passing.

### What we tried

| Step | Action | Test result |
| ---- | ------ | ----------- |
| 1 | Remove ernest-staging from **users** project | 9/9 pass |
| 2 | Remove ernest-staging from **owner** project | 9/9 pass |
| 3 | Remove ernest-staging from **guests** project | 9/9 pass |
| 4 | Wait 15 s, rerun | 9/9 pass |

### Why the tests kept passing

Two separate issues were masking the failure:

#### 2a. `delete_project_membership` silently swallows `ValidationException`

The implementation treats both `ResourceNotFoundException` *and*
`ValidationException` as non-errors (idempotent delete semantics). DataZone
apparently returns `ValidationException` when removing a `PROJECT_OWNER`
designation in certain states, so those deletes silently no-oped.

#### 2b. DataZone `list_project_memberships` has strong eventual consistency lag

Even after the deletions did take effect (confirmed by re-running the delete
and getting `ResourceNotFoundException` — "User not found in project"), the
`list_project_memberships` API continued returning the removed user for an
extended period. Direct inspection showed the user's ID still appearing in
the response with their former designation.

This means the `_is_project_member` implementation — which works by calling
`list_project_memberships` and scanning for the resolved user ID — reads stale
data and can return `True` for a user who has been removed.

**Observed:** ernest-staging's DataZone user ID
`f767271e-c705-43d9-b133-72b3564d773d` continued to appear in the owner
project's raw membership list after deletion.

---

## Mystery 3: Why does `uv run rale` work for Ernest but not Kevin?

Ernest can run `uv run rale` end-to-end and get a TAJ. Kevin gets DENY on the
same package with a properly displayed ARN principal. This raises the question:
are they actually hitting the same authorization path?

### Confirmed: both use the same IAM identity type

`aws sts get-caller-identity` on Ernest's machine returns
`arn:aws:iam::712023778557:user/ernest-staging` — the same pattern as Kevin's
`kevin-staging`. This rules out Ernest having a special admin identity or role.

### Ernest may have Terraform outputs; Kevin may not

`resolve_config` calls `load_terraform_outputs(tf_dir)` where `tf_dir` defaults
to `infra/terraform/`. Ernest has a live Terraform workspace in the repo — the
`rale_authorizer_url`, `rale_router_url`, and `rajee_endpoint` are all read
from `terraform output -json`. Kevin checked out the repo but almost certainly
has **no Terraform state**, so his config comes entirely from env vars or
`~/.config/raja/cli.toml`.

If those URLs differ — e.g. Kevin has a stale or different `RALE_AUTHORIZER_URL`
pointing to a different Lambda version or a different deployment — they could
be hitting different code. Kevin's terminal did show a valid RAJEE endpoint and
reach the authorization phase, so the URLs are configured, but they may not be
identical to Ernest's.

### The most likely remaining cause: `get_user_profile` fails for kevin-staging

The `_is_project_member` implementation has a two-step lookup:

1. `_get_user_id_for_principal(arn)` — calls DataZone `GetUserProfile` with the
   IAM ARN to resolve a DataZone-internal user ID.
2. `list_project_memberships` — scans for that user ID in the project.

If step 1 fails — i.e. DataZone has no user profile for `kevin-staging` at the
**domain level** — `_get_user_id_for_principal` returns `None`, `_is_project_member`
returns `False` for all projects, and `find_project_for_principal` returns `None`
→ DENY.

DataZone user profiles exist at the domain level, separate from project
membership. A user can appear in `list_project_memberships` (added via
`create_project_membership`) without having an active domain-level profile if
they were never provisioned into the domain itself, never signed into the
DataZone portal, or were added by ARN without the domain having seen them
via SSO/IAM Identity Center.

**ernest-staging** has been actively used by Ernest throughout development.
DataZone has definitely resolved and cached a user profile for it.
**kevin-staging** may have been created and added to projects recently without
ever authenticating against the DataZone domain directly, leaving no user
profile for `GetUserProfile` to return.

### Result: hypothesis disproved

All four staging users have ACTIVATED domain-level profiles:

| User | DataZone ID | Status |
| ---- | ----------- | ------ |
| ernest-staging | f767271e-c705-43d9-b133-72b3564d773d | ACTIVATED |
| kevin-staging | ee90040a-df8d-4db8-9a93-62d3536ab212 | ACTIVATED |
| simon-staging | d7c0bac5-8689-4a1a-83c9-222dae89d8ca | ACTIVATED |
| sergey | e42e93e0-72fb-49b4-8a69-1fdbbc89f2f2 | ACTIVATED |

`GetUserProfile` succeeds for all of them. The lookup in `_get_user_id_for_principal`
is not the failure point. The grant path is working correctly when called with
local developer credentials. The mystery of why Kevin gets DENY while Ernest
does not remains unresolved — and we are running out of local explanations.

The remaining candidate is that the **Lambda's execution role** is the variable,
not the principal. Ernest is not running through the Lambda at all when he uses
`uv run rale` locally — he is calling the RALE authorizer endpoint directly over
HTTP, and the Lambda uses its own IAM role to call DataZone. The grant path
works when called from a developer workstation. It may fail inside the Lambda
if the Lambda's role lacks the necessary DataZone permissions.

---

## Open Questions

1. **What is Kevin's actual failure mode?** Now that the CLI surfaces the full
   error body, the next run will tell us whether Kevin's `kevin-staging` user
   hits "principal project not found" (never found by `get_user_profile` or
   `list_project_memberships`) or reaches the package grant check and fails
   there. Given that the Admin UI shows him as a member, and that
   `list_project_memberships` appears to cache aggressively, it's possible his
   membership was *recently added* and DataZone's eventual consistency has
   not propagated to whatever region/endpoint the Lambda is hitting.

2. **Is the Lambda hitting the same DataZone endpoint as our local tests?**
   Local tests run with developer AWS credentials. The Lambda runs with its
   own IAM role. If the Lambda's role lacks `datazone:GetUserProfile` or
   `datazone:ListProjectMemberships`, both calls silently return `None` /
   empty and the user is effectively invisible — producing a DENY.

3. **Should `delete_project_membership` re-raise `ValidationException`?**
   Swallowing it hides real errors (e.g. trying to remove the last owner).
   The idempotent intent only requires ignoring `ResourceNotFoundException`.

4. **Should `_is_project_member` use `get_user_profile` instead of
   `list_project_memberships`?** DataZone's `GetUserProfile` response
   includes project membership and may be more consistent than paginating
   memberships. Worth investigating as an alternative lookup strategy.

---

## Status

- [x] CLI now surfaces distinguishing error detail from the 403 body
- [x] `test_seed_users` integration tests written and passing
- [x] `test_package_grant` integration tests written and passing
- [ ] Root cause of Kevin's specific DENY not yet confirmed (awaiting next run
      with improved error message)
- [x] Ernest's actual STS identity confirmed: `arn:aws:iam::712023778557:user/ernest-staging`
- [ ] Whether `GetUserProfile` succeeds for `kevin-staging` not yet tested
- [ ] Eventual consistency lag not yet mitigated
- [ ] `ValidationException` swallowing in `delete_project_membership` not yet fixed
