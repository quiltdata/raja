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
    },
    {
      label: "Asset type",
      value: `${datazone.asset_type.name} rev ${datazone.asset_type.revision}`,
      meta: "Registered DataZone package asset type",
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
  const principals = state.accessGraph?.principals || [];
  const options = ['<option value="">Choose a principal</option>']
    .concat(
      principals.map((item) => {
        const value = escapeHtml(item.principal);
        const selected = item.principal === state.selectedPrincipal ? " selected" : "";
        return `<option value="${value}"${selected}>${value}</option>`;
      }),
    )
    .join("");

  for (const id of ["global-principal", "revoke-principal-select"]) {
    const el = select(id);
    if (el) el.innerHTML = options;
  }

  const ralePrincipal = select("rale-principal");
  if (ralePrincipal && state.selectedPrincipal && !ralePrincipal.value) {
    ralePrincipal.value = state.selectedPrincipal;
  }
}

function renderPrincipals() {
  const tbody = select("principals-body");
  if (!tbody) return;
  const principals = state.accessGraph?.principals || [];

  tbody.innerHTML = principals
    .map((item) => {
      const lastIssued = state.lastIssuedAt.get(item.principal) || item.last_token_issued;
      return `
        <tr>
          <td>
            <div>${escapeHtml(item.principal)}</div>
            <div class="principal-scopes">${principalScopeMarkup(item.scopes || [])}</div>
          </td>
          <td>${escapeHtml(item.datazone_project_name || item.datazone_project_id || "")}</td>
          <td>${escapeHtml(item.datazone_project_id || "")}</td>
          <td>${escapeHtml(String(item.scope_count ?? (item.scopes || []).length))}</td>
          <td>${escapeHtml(lastIssued ? formatTimestamp(lastIssued) : "Never")}</td>
          <td><button type="button" class="secondary delete-principal" data-principal="${escapeHtml(item.principal)}">Delete</button></td>
        </tr>
      `;
    })
    .join("");

  select("principals-summary-meta").textContent = `${principals.length} principals`;
}

function renderPackages() {
  const tbody = select("packages-body");
  if (!tbody) return;
  const packages = state.accessGraph?.packages || [];
  tbody.innerHTML = packages
    .map((item) => `
      <tr>
        <td>${item.listing_url ? `<a href="${escapeHtml(item.listing_url)}" target="_blank" rel="noopener">${escapeHtml(item.package_name)}</a>` : escapeHtml(item.package_name)}</td>
        <td>${escapeHtml(item.listing_id)}</td>
        <td>${escapeHtml(item.owner_project_name || item.owner_project_id || "")}</td>
        <td>${escapeHtml(item.asset_type || "")}</td>
        <td>${escapeHtml(String(item.subscriptions ?? 0))}</td>
      </tr>
    `)
    .join("");
  select("packages-summary-meta").textContent = `${packages.length} listings`;
}

function renderAccessTable() {
  const tbody = select("access-body");
  if (!tbody) return;
  const rows = state.accessGraph?.access || [];
  tbody.innerHTML = rows
    .map((item) => `
      <tr>
        <td>${escapeHtml(item.principal_project_name || item.principal_project_id || "")}</td>
        <td>${escapeHtml(item.package_name || "")}</td>
        <td>${escapeHtml(item.access_mode || "")}</td>
        <td>${escapeHtml(item.source || "")}</td>
        <td>${escapeHtml(item.subscription_id || "")}</td>
      </tr>
    `)
    .join("");
  select("access-summary-meta").textContent = `${rows.length} relationships`;
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
  renderPrincipals();
  renderPackages();
  renderAccessTable();
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

function scopesForMode(mode) {
  if (mode === "owner") return ["*:*:*"];
  if (mode === "user") return ["S3Object:*:*"];
  return ["S3Object:*:s3:GetObject"];
}

async function createPrincipal(event) {
  event.preventDefault();
  const principal = select("principal-input").value.trim();
  if (!principal) {
    setStatusBanner("principal-form-status", "Principal ID is required.", "warn");
    return;
  }

  const mode = select("principal-scope-mode").value;
  const result = await apiFetch("/principals", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ principal, scopes: scopesForMode(mode) }),
  });
  if (!result.ok) {
    setStatusBanner("principal-form-status", result.data.detail || "Unable to add principal.", "error");
    return;
  }

  select("principal-input").value = "";
  setStatusBanner("principal-form-status", `Added ${principal}.`, "ok");
  await loadAccessGraph();
}

async function deletePrincipal(principal) {
  const result = await apiFetch(`/principals/${encodeURIComponent(principal)}`, {
    method: "DELETE",
  });
  if (!result.ok) {
    select("revoke-soft-output").textContent = result.data.detail || "Delete failed.";
    return;
  }
  select("revoke-soft-output").textContent = result.data.message || `Deleted ${principal}.`;
  if (state.selectedPrincipal === principal) {
    state.selectedPrincipal = "";
  }
  await loadAccessGraph();
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

async function onPackageSelected() {
  const packageName = select("rale-package-select").value;
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
    renderPrincipals();
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
    select("column-structure").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  select("structure-refresh").addEventListener("click", loadStructure);
  select("principal-form").addEventListener("submit", createPrincipal);

  select("global-principal").addEventListener("change", async (event) => {
    state.selectedPrincipal = event.target.value;
    select("rale-principal").value = state.selectedPrincipal;
    await loadAccessGraph();
  });

  document.body.addEventListener("click", async (event) => {
    const principalButton = event.target.closest(".delete-principal");
    if (principalButton) {
      await deletePrincipal(principalButton.dataset.principal || "");
      return;
    }

    const failureButton = event.target.closest(".run-failure-test");
    if (failureButton) {
      await runFailureTest(failureButton.dataset.testId || "");
    }
  });

  select("revoke-refresh").addEventListener("click", loadAccessGraph);
  select("revoke-delete").addEventListener("click", async () => {
    const principal = select("revoke-principal-select").value;
    if (!principal) {
      select("revoke-soft-output").textContent = "Choose a principal first.";
      return;
    }
    await deletePrincipal(principal);
  });

  select("rale-reset").addEventListener("click", resetRaleFlow);
  select("rale-package-select").addEventListener("change", onPackageSelected);
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
