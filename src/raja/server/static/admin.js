const select = (id) => document.getElementById(id);
const ADMIN_KEY_STORAGE_KEY = "raja_admin_key";
const API_BASE = window.location.href.split("#")[0].replace(/\/+$/, "");

const state = {
  structure: null,
  accessGraph: null,
  selectedPrincipal: "",
  lastIssuedAt: new Map(),
  failureDefinitions: [],
  failureCategories: [],
  failureResults: [],
  selectedPackage: "",
  selectedFile: null,
  currentUsl: "",
  currentTaj: "",
};

function getAdminKey() {
  return sessionStorage.getItem(ADMIN_KEY_STORAGE_KEY) || "";
}

function setAdminKey(value) {
  sessionStorage.setItem(ADMIN_KEY_STORAGE_KEY, value);
}

function setAuthState(kind) {
  const lock = select("auth-lock");
  if (!lock) return;
  lock.className = `auth-lock ${kind}`;
  const labels = {
    unknown: "Not verified",
    ok: "Authenticated",
    fail: "Authentication failed",
  };
  const label = labels[kind] || labels.unknown;
  lock.title = label;
  lock.setAttribute("aria-label", label);
}

function setAdminAuthError(message) {
  const banner = select("admin-auth-error");
  if (!banner) return;
  banner.hidden = !message;
  if (message) banner.textContent = message;
}

function setHeaderHealthLabel(message, kind = "") {
  const dot = select("header-health-dot");
  const text = select("header-health-text");
  if (!dot || !text) return;
  dot.className = "dot";
  if (kind) dot.classList.add(kind);
  text.textContent = message;
}

function clearStatusClasses(el) {
  if (!el) return;
  el.classList.remove("ok", "warn", "error");
}

function setStatusBanner(id, text, kind = "") {
  const el = select(id);
  setStatusNode(el, text, kind);
}

function setStatusNode(el, text, kind = "") {
  if (!el) return;
  el.textContent = text;
  clearStatusClasses(el);
  if (kind) el.classList.add(kind);
}

function formatJson(data) {
  return JSON.stringify(data, null, 2);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatTimestamp(epochSeconds) {
  if (!epochSeconds) return "Never";
  return new Date(epochSeconds * 1000).toLocaleString();
}

function formatByteCount(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function statusKindFromValue(value) {
  if (!value) return "warn";
  const raw = String(value).toLowerCase();
  if (raw === "ok") return "ok";
  if (raw === "accepted" || raw === "succeeded") return "ok";
  if (raw === "rejected" || raw === "failed") return "error";
  if (raw.startsWith("error")) return "error";
  return "warn";
}

function statusBadgeText(value) {
  const kind = statusKindFromValue(value);
  if (kind === "ok") return "OK";
  if (kind === "error") return "Error";
  return "Warn";
}

function statusDetailText(value) {
  if (!value) return "";
  const raw = String(value);
  const lower = raw.toLowerCase();
  if (lower === "ok" || lower === "warn") return "";
  if (lower.startsWith("error:")) return raw.slice(6).trim();
  return raw;
}

function scopesForProject(projectKey) {
  if (projectKey === "owner") return ["*:*:*"];
  if (projectKey === "users") return ["S3Object:*:*"];
  return ["S3Object:*:s3:GetObject"];
}

function dedupePrincipals() {
  const summary = state.accessGraph?.principal_summary;
  if (Array.isArray(summary) && summary.length) return summary;

  const grouped = new Map();
  for (const item of state.accessGraph?.principals || []) {
    if (!grouped.has(item.principal)) {
      grouped.set(item.principal, {
        principal: item.principal,
        project_ids: [],
        project_names: [],
      });
    }
    const row = grouped.get(item.principal);
    if (item.datazone_project_id && !row.project_ids.includes(item.datazone_project_id)) {
      row.project_ids.push(item.datazone_project_id);
    }
    if (item.datazone_project_name && !row.project_names.includes(item.datazone_project_name)) {
      row.project_names.push(item.datazone_project_name);
    }
  }
  return Array.from(grouped.values());
}

async function parseResponse(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json();
  }
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
}

async function apiFetch(endpoint, options = {}) {
  const headers = new Headers(options.headers || {});
  const key = getAdminKey();
  if (key) headers.set("Authorization", `Bearer ${key}`);

  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), 20000);
  let response;
  try {
    response = await fetch(API_BASE + endpoint, {
      ...options,
      headers,
      cache: "no-store",
      signal: controller.signal,
    });
  } finally {
    window.clearTimeout(timeoutId);
  }

  if (response.status === 401) {
    setAdminAuthError("Invalid or missing admin key.");
    setAuthState("fail");
  } else if (response.ok) {
    setAdminAuthError("");
  }

  const data = await parseResponse(response);
  return { ok: response.ok, status: response.status, data };
}

function updateHeaderHealth() {
  const dot = select("header-health-dot");
  const text = select("header-health-text");
  if (!dot || !text) return;

  const components = [];
  if (state.structure?.datazone?.domain?.status) components.push(state.structure.datazone.domain.status);
  for (const item of Object.values(state.structure?.stack || {})) {
    components.push(item?.health?.status || "warn");
  }

  const errors = components.filter((item) => String(item).startsWith("error")).length;
  const warns = components.filter((item) => String(item) === "warn").length;
  dot.className = "dot";

  const warnComponents = Object.values(state.structure?.stack || {})
    .filter((item) => (item?.health?.status || "warn") === "warn")
    .map((item) => {
      const code = item.health?.status_code ? ` (HTTP ${item.health.status_code})` : "";
      const detail = item.health?.detail ? `: ${item.health.detail}` : "";
      return `${item.label}${code}${detail}`;
    });
  const errorComponents = Object.values(state.structure?.stack || {})
    .filter((item) => String(item?.health?.status || "").startsWith("error"))
    .map((item) => {
      const detail = item.health?.detail ? `: ${item.health.detail}` : "";
      return `${item.label}${detail}`;
    });

  if (errors) {
    dot.classList.add("bad");
    text.textContent = `${errors} component${errors === 1 ? "" : "s"} unreachable`;
    dot.title = errorComponents.join("\n");
    return;
  }
  if (warns) {
    dot.classList.add("warn");
    text.textContent = "Partial visibility";
    dot.title = warnComponents.join("\n");
    return;
  }
  dot.classList.add("ok");
  dot.title = "";
  text.textContent = "All systems live";
}

async function reloadAdminState() {
  const key = getAdminKey();
  if (!key) {
    setAuthState("unknown");
    setHeaderHealthLabel("Admin key required");
    setAdminAuthError("Enter the admin key to load the control plane.");
    return;
  }

  setAuthState("unknown");
  setHeaderHealthLabel("Loading control plane");
  await refreshAll();
  setAuthState("ok");
  if (!state.selectedPrincipal && state.accessGraph?.principals?.length) {
    state.selectedPrincipal = state.accessGraph.principals[0].principal;
    renderPrincipalSelectors();
    select("rale-principal").value = state.selectedPrincipal;
  }
}

function renderStatusRows() {
  const datazoneRows = select("datazone-status-rows");
  const stackRows = select("stack-status-rows");
  if (!datazoneRows || !stackRows || !state.structure) return;

  const { datazone, stack } = state.structure;
  const datazoneItems = [
    {
      label: "Domain",
      value: `${datazone.domain.name} (${datazone.domain.id})`,
      meta: [datazone.domain.region, datazone.domain.portal_url].filter(Boolean).join(" · "),
      status: datazone.domain.status,
      href: datazone.domain.portal_url,
    },
    {
      label: "Owner project",
      value: `${datazone.owner_project.name} (${datazone.owner_project.id || "unset"})`,
      meta: "Seeder project that owns published listings",
      status: datazone.owner_project.status,
      href: datazone.owner_project.portal_url,
    },
    {
      label: "Users project",
      value: `${datazone.users_project.name} (${datazone.users_project.id || "unset"})`,
      meta: "Project with broad package access",
      status: datazone.users_project.status,
      href: datazone.users_project.portal_url,
    },
    {
      label: "Guests project",
      value: `${datazone.guests_project.name} (${datazone.guests_project.id || "unset"})`,
      meta: "Project with read-only package access",
      status: datazone.guests_project.status,
      href: datazone.guests_project.portal_url,
    },
    {
      label: "Asset type",
      value: `${datazone.asset_type.name} rev ${datazone.asset_type.revision}`,
      meta: "Registered SageMaker package asset type",
      status: datazone.asset_type.status,
    },
  ];

  datazoneRows.innerHTML = datazoneItems
    .map((item) => {
      const statusKind = statusKindFromValue(item.status);
      const statusDetail = statusDetailText(item.status);
      const label = item.href
        ? `<a href="${escapeHtml(item.href)}" target="_blank" rel="noopener">${escapeHtml(item.label)}</a>`
        : escapeHtml(item.label);
      const metaParts = [item.meta, statusDetail].filter(Boolean);
      return `
        <div class="status-row">
          <div class="status-row-main">
            <strong>${label}</strong>
            <div>${escapeHtml(item.value)}</div>
            <div class="status-row-meta">${escapeHtml(metaParts.join(" · "))}</div>
          </div>
          <span class="badge ${statusKind}">${escapeHtml(statusBadgeText(item.status))}</span>
        </div>
      `;
    })
    .join("");

  stackRows.innerHTML = Object.values(stack)
    .map((item) => {
      const status = item.health?.status || "warn";
      const statusKind = statusKindFromValue(status);
      const meta = [
        item.url,
        item.health?.status_code ? `HTTP ${item.health.status_code}` : "",
        item.kids?.length ? `KIDs: ${item.kids.join(", ")}` : "",
        item.health?.detail || "",
        statusDetailText(status),
      ]
        .filter(Boolean)
        .join(" · ");
      const value = item.url
        ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">${escapeHtml(item.label)}</a>`
        : escapeHtml(item.label);
      const badgeTooltip =
        statusKind !== "ok"
          ? [
              item.health?.status_code ? `HTTP ${item.health.status_code}` : "",
              item.health?.detail || "",
              statusDetailText(status),
            ]
              .filter(Boolean)
              .join(" · ")
          : "";
      return `
        <div class="status-row">
          <div class="status-row-main">
            <strong>${value}</strong>
            <div class="status-row-meta">${escapeHtml(meta || "No detail returned")}</div>
          </div>
          <span class="badge ${statusKind}"${badgeTooltip ? ` title="${escapeHtml(badgeTooltip)}"` : ""}>${escapeHtml(statusBadgeText(status))}</span>
        </div>
      `;
    })
    .join("");

  select("datazone-summary-meta").textContent = datazone.domain.name || "Loaded";
  select("stack-summary-meta").textContent = `${Object.keys(stack).length} components`;
}

function principalScopeMarkup(scopes = []) {
  return scopes.map((scope) => `<span class="scope-chip">${escapeHtml(scope)}</span>`).join("");
}

function renderPrincipalSelectors() {
  const principals = dedupePrincipals();
  const options = ['<option value="">Choose a principal</option>']
    .concat(
      principals.map((item) => {
        const value = escapeHtml(item.principal);
        const selected = item.principal === state.selectedPrincipal ? " selected" : "";
        return `<option value="${value}"${selected}>${value}</option>`;
      }),
    )
    .join("");

  for (const id of ["global-principal"]) {
    const el = select(id);
    if (el) el.innerHTML = options;
  }

  const ralePrincipal = select("rale-principal");
  if (ralePrincipal && state.selectedPrincipal && !ralePrincipal.value) {
    ralePrincipal.value = state.selectedPrincipal;
  }
}

function renderProjects() {
  const principals = state.accessGraph?.principals || [];
  const projectRows = new Map(
    ["owner", "users", "guests"].map((projectKey) => [projectKey, []]),
  );
  const datazone = state.structure?.datazone || {};
  const projectConfig = {
    owner: datazone.owner_project,
    users: datazone.users_project,
    guests: datazone.guests_project,
  };

  for (const [projectKey, project] of Object.entries(projectConfig)) {
    const meta = select(`project-${projectKey}-meta`);
    const link = select(`project-${projectKey}-link`);
    const scope = select(`project-${projectKey}-scope`);
    if (meta) {
      meta.textContent = project?.id ? `${project.name} (${project.id})` : "Project not configured";
    }
    if (link) {
      if (project?.portal_url) {
        link.href = project.portal_url;
        link.hidden = false;
      } else {
        link.hidden = true;
        link.removeAttribute("href");
      }
    }
    if (scope) {
      scope.textContent = scopesForProject(projectKey).join(", ");
    }
  }

  for (const item of principals) {
    const projectKey = Object.entries(projectConfig).find(
      ([, project]) => project?.id && project.id === item.datazone_project_id,
    )?.[0];
    if (!projectKey) continue;
    projectRows.get(projectKey).push(item);
  }

  for (const [projectKey, items] of projectRows.entries()) {
    const tbody = select(`project-${projectKey}-body`);
    if (!tbody) continue;
    tbody.innerHTML = items.length
      ? items
          .map((item) => {
            const lastIssued = state.lastIssuedAt.get(item.principal) || item.last_token_issued;
            return `
              <tr>
                <td>
                  <div>${escapeHtml(item.principal)}</div>
                  <div class="principal-scopes">${principalScopeMarkup(item.scopes || [])}</div>
                </td>
                <td>${escapeHtml(lastIssued ? formatTimestamp(lastIssued) : "Never")}</td>
                <td>
                  <button
                    type="button"
                    class="secondary delete-principal"
                    data-principal="${escapeHtml(item.principal)}"
                    data-project-id="${escapeHtml(item.datazone_project_id || "")}"
                  >Remove from project</button>
                </td>
              </tr>
            `;
          })
          .join("")
      : '<tr><td colspan="3">No members</td></tr>';
  }

  const uniquePrincipals = dedupePrincipals();
  const projectsSummary = select("projects-summary-meta");
  if (projectsSummary) {
    projectsSummary.textContent = `${uniquePrincipals.length} principals across ${projectRows.size} projects`;
  }
}

function renderPackages() {
  const tbody = select("packages-body");
  if (!tbody) return;
  const packages = state.accessGraph?.packages || [];
  tbody.innerHTML = packages
    .map(
      (item) => `
        <tr>
          <td>${item.listing_url ? `<a href="${escapeHtml(item.listing_url)}" target="_blank" rel="noopener">${escapeHtml(item.package_name)}</a>` : escapeHtml(item.package_name)}</td>
          <td>${escapeHtml(item.listing_id)}</td>
          <td>${item.owner_project_url ? `<a href="${escapeHtml(item.owner_project_url)}" target="_blank" rel="noopener">${escapeHtml(item.owner_project_name || item.owner_project_id || "")}</a>` : escapeHtml(item.owner_project_name || item.owner_project_id || "")}</td>
          <td>${escapeHtml(item.asset_type || "")}</td>
          <td>${item.listing_url ? `<a href="${escapeHtml(item.listing_url)}" target="_blank" rel="noopener">${escapeHtml(String(item.subscriptions ?? 0))}</a>` : escapeHtml(String(item.subscriptions ?? 0))}</td>
          <td><button type="button" class="secondary use-in-rale" data-package="${escapeHtml(item.package_name || "")}">${item.package_name === state.selectedPackage ? "Selected for flow" : "Select for flow"}</button></td>
        </tr>
      `,
    )
    .join("");
  select("packages-summary-meta").textContent = `${packages.length} listings`;
}

function renderSubscriptions() {
  const tbody = select("subscriptions-body");
  if (!tbody) return;
  const rows = state.accessGraph?.subscriptions || [];
  tbody.innerHTML = rows.length
    ? rows
        .map(
          (item) => `
            <tr>
              <td>${escapeHtml(item.package_name || "")}</td>
              <td>${escapeHtml(item.owner_project_name || item.owner_project_id || "")}</td>
              <td>${escapeHtml(item.consumer_project_name || item.consumer_project_id || "")}</td>
              <td><span class="badge ${statusKindFromValue(item.status)}">${escapeHtml(item.status || "UNKNOWN")}</span></td>
              <td>${item.subscription_url ? `<a href="${escapeHtml(item.subscription_url)}" target="_blank" rel="noopener">${escapeHtml(item.subscription_id || "")}</a>` : escapeHtml(item.subscription_id || "")}</td>
            </tr>
          `,
        )
        .join("")
    : '<tr><td colspan="5">No subscriptions</td></tr>';
  select("subscriptions-summary-meta").textContent = `${rows.length} subscriptions`;
}

function renderRalePackages(packages, registry) {
  const selectEl = select("rale-package-select");
  if (!selectEl) return;
  const options = ['<option value="">Choose a package</option>']
    .concat(packages.map((pkg) => `<option value="${escapeHtml(pkg)}">${escapeHtml(pkg)}</option>`))
    .join("");
  selectEl.innerHTML = options;
  select("rale-select-status").textContent = registry
    ? `Registry: ${registry}`
    : "No registry configured.";
}

function renderRaleFiles(files) {
  const fileSelect = select("rale-file-select");
  const fileList = select("rale-file-list");
  if (!fileSelect || !fileList) return;

  if (!files.length) {
    fileSelect.disabled = true;
    fileSelect.innerHTML = '<option value="">No files found</option>';
    fileList.innerHTML = "";
    return;
  }

  fileSelect.disabled = false;
  fileSelect.innerHTML = ['<option value="">Choose a file</option>']
    .concat(
      files.map((file, index) => `
        <option value="${index}">${escapeHtml(file.path)} (${escapeHtml(formatByteCount(file.size))})</option>
      `),
    )
    .join("");

  fileList.innerHTML = files
    .map(
      (file) => `
        <div class="mini-row">
          <span>${escapeHtml(file.path)}</span>
          <span>${escapeHtml(formatByteCount(file.size))}</span>
        </div>
      `,
    )
    .join("");
}

function renderClaims(claims = []) {
  const root = select("rale-claims");
  if (!root) return;
  root.innerHTML = claims
    .map((claim) => `
      <div class="claim-card">
        <div class="claim-key">${escapeHtml(claim.key)}</div>
        <div class="claim-value">${escapeHtml(Array.isArray(claim.value) ? claim.value.join(", ") : String(claim.value ?? ""))}</div>
        <p class="claim-explanation">${escapeHtml(claim.explanation)}</p>
      </div>
    `)
    .join("");
}

function renderFailureDefinitions() {
  const categorySelect = select("failure-category-select");
  const list = select("failure-list");
  if (!categorySelect || !list) return;

  categorySelect.innerHTML = ['<option value="">Choose a category</option>']
    .concat(
      state.failureCategories.map(
        (cat) => `<option value="${escapeHtml(cat.id)}">${escapeHtml(cat.label)}</option>`,
      ),
    )
    .join("");

  list.innerHTML = state.failureDefinitions
    .map((test) => `
      <div class="failure-card" data-category="${escapeHtml(test.category)}">
        <div class="failure-head">
          <div>
            <h3 class="failure-title">${escapeHtml(test.id)} · ${escapeHtml(test.title)}</h3>
            <p class="failure-meta">${escapeHtml(test.category_label)} · ${escapeHtml(test.priority)}</p>
          </div>
          <button type="button" class="secondary run-failure-test" data-test-id="${escapeHtml(test.id)}">Run</button>
        </div>
        <p class="failure-copy">${escapeHtml(test.description)}</p>
        <p class="failure-copy"><strong>Hypothesis:</strong> ${escapeHtml(test.expected_summary)}</p>
        <div class="inline-status" id="failure-result-${escapeHtml(test.id).replaceAll(".", "-")}">Not run yet.</div>
      </div>
    `)
    .join("");

  select("failures-summary-meta").textContent = `${state.failureDefinitions.length} tests`;
}

function recordFailureResult(result) {
  state.failureResults.push(result);
  const target = select(`failure-result-${result.test_id.replaceAll(".", "-")}`);
  if (!target) return;
  target.textContent = `${result.status}: expected ${result.expected}; actual ${result.actual}`;
  target.className = "inline-status";
  if (result.status === "PASS") target.classList.add("ok");
  if (result.status === "FAIL") target.classList.add("error");
  if (result.status === "ERROR" || result.status === "NOT_IMPLEMENTED") target.classList.add("warn");
  select("failure-export-results").disabled = state.failureResults.length === 0;
}

async function loadStructure() {
  const result = await apiFetch("/admin/structure");
  if (!result.ok) {
    setStatusBanner("rale-select-status", result.data.detail || "Unable to load structure.", "error");
    return;
  }
  state.structure = result.data;
  renderStatusRows();
  updateHeaderHealth();
}

async function loadAccessGraph() {
  const query = state.selectedPrincipal ? `?principal=${encodeURIComponent(state.selectedPrincipal)}` : "";
  const result = await apiFetch(`/admin/access-graph${query}`);
  if (!result.ok) {
    setStatusBanner("principal-form-status", result.data.detail || "Unable to load access graph.", "error");
    return;
  }
  state.accessGraph = result.data;
  renderPrincipalSelectors();
  renderProjects();
  renderSubscriptions();
  renderPackages();
}

async function loadRalePackages() {
  const result = await apiFetch("/admin/rale/packages");
  if (!result.ok) {
    setStatusBanner("rale-select-status", result.data.detail || "Unable to load registry packages.", "error");
    return;
  }
  renderRalePackages(result.data.packages || [], result.data.registry || "");
}

async function loadFailureDefinitions() {
  const result = await apiFetch("/api/failure-tests/");
  if (!result.ok) {
    select("failure-summary").textContent = result.data.detail || "Unable to load failure tests.";
    return;
  }
  state.failureDefinitions = result.data.tests || [];
  state.failureCategories = result.data.categories || [];
  renderFailureDefinitions();
}

async function refreshAll() {
  await loadStructure();
  await loadAccessGraph();
  await loadRalePackages();
  await loadFailureDefinitions();
}

async function createPrincipal(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const principal = form.elements.principal?.value.trim();
  const statusNode = form.parentElement?.querySelector('[data-role="project-membership-status"]');
  if (!principal) {
    setStatusNode(statusNode, "Principal ARN is required.", "warn");
    return;
  }

  const projectKey = form.dataset.projectKey || "guests";
  const result = await apiFetch("/principals", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ principal, scopes: scopesForProject(projectKey) }),
  });
  if (!result.ok) {
    setStatusNode(statusNode, result.data.detail || "Unable to add principal to project.", "error");
    return;
  }

  form.reset();
  setStatusNode(statusNode, `Added ${principal} to project.`, "ok");
  await loadAccessGraph();
}

async function deletePrincipal(principal, projectId = "", statusNode = null) {
  const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
  const result = await apiFetch(`/principals/${encodeURIComponent(principal)}${query}`, {
    method: "DELETE",
  });
  if (!result.ok) {
    setStatusNode(statusNode, result.data.detail || "Removal failed.", "error");
    return;
  }
  setStatusNode(statusNode, result.data.message || `Removed ${principal} from project.`, "ok");
  await loadAccessGraph();
  if (
    state.selectedPrincipal === principal &&
    !dedupePrincipals().some((item) => item.principal === principal)
  ) {
    state.selectedPrincipal = "";
    renderPrincipalSelectors();
  }
}

async function selectPackageForRale(packageName, options = {}) {
  if (!packageName) return;
  state.selectedPackage = packageName;
  const packageSelect = select("rale-package-select");
  if (packageSelect) packageSelect.value = packageName;
  await onPackageSelected(packageName);
  renderPackages();
  if (options.scroll !== false) {
    select("section-rale")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function resetRaleFlow() {
  state.selectedPackage = "";
  state.selectedFile = null;
  state.currentUsl = "";
  state.currentTaj = "";
  select("rale-package-select").value = "";
  select("rale-file-select").disabled = true;
  select("rale-file-select").innerHTML = '<option value="">Choose a package first</option>';
  select("rale-usl").value = "";
  select("rale-file-list").innerHTML = "";
  select("rale-authorize-button").disabled = true;
  select("rale-deliver-button").disabled = true;
  renderClaims([]);
  setStatusBanner("rale-authorize-status", "Select a package and file first.");
  setStatusBanner("rale-deliver-status", "Authorize first to unlock delivery.");
  select("rale-deliver-diagnostics").textContent = "No delivery attempted yet.";
  select("rale-deliver-preview").textContent = "No content yet.";
  select("rale-summary-meta").textContent = "Reset";
}

async function onPackageSelected(explicitPackageName = "") {
  const packageName = explicitPackageName || select("rale-package-select").value;
  resetRaleFlow();
  select("rale-package-select").value = packageName;
  if (!packageName) return;
  state.selectedPackage = packageName;

  const result = await apiFetch(`/admin/rale/package-files?package_name=${encodeURIComponent(packageName)}`);
  if (!result.ok) {
    setStatusBanner("rale-select-status", result.data.detail || "Unable to load package files.", "error");
    return;
  }

  state.packageFiles = result.data.files || [];
  renderRaleFiles(state.packageFiles);
  setStatusBanner(
    "rale-select-status",
    `${packageName}@${result.data.manifest_hash} loaded with ${state.packageFiles.length} files.`,
    "ok",
  );
}

function onFileSelected() {
  const index = Number(select("rale-file-select").value);
  if (!Number.isInteger(index) || !state.packageFiles?.[index]) {
    select("rale-usl").value = "";
    select("rale-authorize-button").disabled = true;
    return;
  }
  state.selectedFile = state.packageFiles[index];
  state.currentUsl = state.selectedFile.usl;
  select("rale-usl").value = state.currentUsl;
  select("rale-authorize-button").disabled = false;
  select("rale-summary-meta").textContent = "Select complete";
}

async function authorizeRale(event) {
  event.preventDefault();
  const principal = select("rale-principal").value.trim() || state.selectedPrincipal;
  if (!principal || !state.currentUsl) {
    setStatusBanner("rale-authorize-status", "Principal and USL are required.", "warn");
    return;
  }

  const result = await apiFetch("/admin/rale/authorize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ principal, usl: state.currentUsl }),
  });
  if (!result.ok) {
    setStatusBanner("rale-authorize-status", result.data.detail || "Authorization failed.", "error");
    renderClaims([]);
    return;
  }

  const { status_code: statusCode, body, annotated_claims: annotatedClaims } = result.data;
  if (statusCode === 200 && body.token) {
    state.currentTaj = body.token;
    renderClaims(annotatedClaims || []);
    select("rale-deliver-button").disabled = false;
    state.lastIssuedAt.set(principal, Math.floor(Date.now() / 1000));
    renderProjects();
    setStatusBanner(
      "rale-authorize-status",
      `TAJ issued for ${principal}. Claims are decoded inline below.`,
      "ok",
    );
    select("rale-summary-meta").textContent = "Authorized";
    return;
  }

  const denial = body.error || body.detail || body.decision || "Denied";
  renderClaims([]);
  setStatusBanner(
    "rale-authorize-status",
    `Denied (${statusCode}). ${denial}`,
    statusCode === 403 ? "warn" : "error",
  );
}

async function deliverRale() {
  if (!state.currentTaj || !state.currentUsl) {
    setStatusBanner("rale-deliver-status", "Authorize before delivery.", "warn");
    return;
  }

  const result = await apiFetch("/admin/rale/deliver", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ usl: state.currentUsl, taj: state.currentTaj }),
  });
  if (!result.ok) {
    setStatusBanner("rale-deliver-status", result.data.detail || "Delivery failed.", "error");
    return;
  }

  const payload = result.data;
  setStatusBanner(
    "rale-deliver-status",
    `Router returned ${payload.status_code}; ${formatByteCount(payload.byte_count)} transferred.`,
    payload.status_code === 200 ? "ok" : "warn",
  );
  select("rale-deliver-diagnostics").textContent = formatJson({
    status_code: payload.status_code,
    byte_count: payload.byte_count,
    content_type: payload.content_type,
    diagnostics: payload.diagnostics,
    headers: payload.headers,
  });
  select("rale-deliver-preview").textContent = payload.preview || "(binary content or empty response)";
  select("rale-summary-meta").textContent = "Delivered";
}

async function runFailureTest(testId) {
  const result = await apiFetch(`/api/failure-tests/${encodeURIComponent(testId)}/run`, {
    method: "POST",
  });
  if (!result.ok) {
    recordFailureResult({
      test_id: testId,
      status: "ERROR",
      expected: "Failure test should run",
      actual: result.data.detail || "Failed to execute",
    });
    return;
  }
  recordFailureResult(result.data);
}

async function runFailureCategory(category) {
  const result = await apiFetch(`/api/failure-tests/categories/${encodeURIComponent(category)}/run`, {
    method: "POST",
  });
  if (!result.ok) {
    select("failure-summary").textContent = result.data.detail || "Category run failed.";
    return;
  }
  for (const run of result.data.results || []) {
    recordFailureResult(run);
  }
  select("failure-summary").textContent = `Ran ${result.data.results.length} tests in ${category}.`;
}

async function runAllFailures() {
  for (const category of state.failureCategories) {
    await runFailureCategory(category.id);
  }
}

function exportFailureResults() {
  const blob = new Blob([JSON.stringify(state.failureResults, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "raja-failure-results.json";
  link.click();
  URL.revokeObjectURL(url);
}

async function rotateSecret() {
  const ok = window.confirm("Rotate the signing secret and invalidate all existing tokens?");
  if (!ok) return;
  const result = await apiFetch("/admin/rotate-secret", { method: "POST" });
  if (!result.ok) {
    select("revoke-hard-output").textContent = result.data.detail || "Secret rotation failed.";
    return;
  }
  const item = document.createElement("li");
  item.textContent = `${new Date().toLocaleString()} · ${result.data.status} · ${result.data.operation_id}`;
  select("rotate-timeline").prepend(item);
  select("revoke-hard-output").textContent = formatJson(result.data);
}

function bindEvents() {
  select("admin-key").value = getAdminKey();
  select("admin-key").addEventListener("input", (event) => {
    setAdminKey(event.target.value.trim());
    setAuthState(getAdminKey() ? "unknown" : "fail");
    setHeaderHealthLabel(getAdminKey() ? "Press Enter to load" : "Admin key required");
  });

  select("admin-key").addEventListener("keydown", async (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    setAdminKey(event.target.value.trim());
    await reloadAdminState();
  });

  select("admin-key-toggle").addEventListener("click", () => {
    const input = select("admin-key");
    const button = select("admin-key-toggle");
    input.type = input.type === "password" ? "text" : "password";
    button.textContent = input.type === "password" ? "Show" : "Hide";
  });

  select("header-health-chip").addEventListener("click", () => {
    select("section-stack").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  select("structure-refresh").addEventListener("click", loadStructure);
  for (const form of document.querySelectorAll(".project-principal-form")) {
    form.addEventListener("submit", createPrincipal);
  }

  select("global-principal").addEventListener("change", async (event) => {
    state.selectedPrincipal = event.target.value;
    select("rale-principal").value = state.selectedPrincipal;
    await loadAccessGraph();
  });

  document.body.addEventListener("click", async (event) => {
    const principalButton = event.target.closest(".delete-principal");
    if (principalButton) {
      const statusNode = principalButton.closest(".project-card")?.querySelector('[data-role="principal-form-status"]');
      await deletePrincipal(
        principalButton.dataset.principal || "",
        principalButton.dataset.projectId || "",
        statusNode,
      );
      return;
    }

    const packageButton = event.target.closest(".use-in-rale");
    if (packageButton) {
      await selectPackageForRale(packageButton.dataset.package || "");
      return;
    }

    const failureButton = event.target.closest(".run-failure-test");
    if (failureButton) {
      await runFailureTest(failureButton.dataset.testId || "");
    }
  });

  select("rale-reset").addEventListener("click", resetRaleFlow);
  select("rale-package-select").addEventListener("change", () => onPackageSelected());
  select("rale-file-select").addEventListener("change", onFileSelected);
  select("rale-authorize-form").addEventListener("submit", authorizeRale);
  select("rale-deliver-button").addEventListener("click", deliverRale);

  select("failure-run-category").addEventListener("click", async () => {
    const category = select("failure-category-select").value;
    if (!category) {
      select("failure-summary").textContent = "Choose a category first.";
      return;
    }
    await runFailureCategory(category);
  });
  select("failure-run-all").addEventListener("click", runAllFailures);
  select("failure-export-results").addEventListener("click", exportFailureResults);
  select("failure-category-select").addEventListener("change", (event) => {
    const category = event.target.value;
    for (const card of document.querySelectorAll(".failure-card")) {
      card.hidden = Boolean(category) && card.dataset.category !== category;
    }
  });

  select("rotate-secret").addEventListener("click", rotateSecret);
}

async function init() {
  bindEvents();
  resetRaleFlow();
  await reloadAdminState();
}

init().catch((error) => {
  console.error(error);
  setAuthState("fail");
  setAdminAuthError(String(error));
});
