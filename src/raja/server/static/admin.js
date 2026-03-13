const select = (id) => document.getElementById(id);
const ADMIN_KEY_STORAGE_KEY = "raja_admin_key";
const DEFAULT_VIEW = "overview";
const VIEW_IDS = ["overview", "authority", "token", "enforce", "failures", "incident", "audit", "about"];
// Base URL for API calls — strips fragment and trailing slash so paths like
// "/health" work correctly even when served under an API Gateway stage prefix.
const API_BASE = window.location.href.split("#")[0].replace(/\/+$/, "");

const state = {
  loadedViews: new Set(),
  rotateOperationId: null,
  rotatePollTimer: null,
};

function getAdminKey() {
  return sessionStorage.getItem(ADMIN_KEY_STORAGE_KEY) || "";
}

function setAdminKey(key) {
  sessionStorage.setItem(ADMIN_KEY_STORAGE_KEY, key);
}

function setAdminAuthError(message) {
  const el = select("admin-auth-error");
  if (!el) {
    return;
  }
  if (!message) {
    el.hidden = true;
    return;
  }
  el.textContent = message;
  el.hidden = false;
}

function setAuthState(state) {
  const el = select("auth-lock");
  if (!el) return;
  el.classList.remove("unknown", "ok", "fail");
  el.classList.add(state);
  const labels = {
    unknown: "Not verified",
    ok: "Authenticated",
    fail: "Authentication failed",
  };
  const label = labels[state] || "Not verified";
  el.title = label;
  el.setAttribute("aria-label", label);
  el.setAttribute("data-tooltip", label);
}

let _pingAuthTimer = null;

async function pingAuth() {
  if (!getAdminKey()) {
    setAuthState("unknown");
    return;
  }
  try {
    const result = await apiFetch("/principals", { method: "GET" });
    if (result.ok) {
      setAuthState("ok");
    }
    // 401 case is already handled inside apiFetch → setAuthState("fail")
  } catch {
    // network error — leave existing state
  }
}

function schedulePingAuth() {
  if (_pingAuthTimer) window.clearTimeout(_pingAuthTimer);
  _pingAuthTimer = window.setTimeout(pingAuth, 600);
}

function clearStatusClasses(el) {
  if (!el) {
    return;
  }
  el.classList.remove("valid", "invalid", "warn");
}

function setStatusBanner(id, text, kind = "") {
  const el = select(id);
  if (!el) {
    return;
  }
  el.textContent = text;
  clearStatusClasses(el);
  if (kind) {
    el.classList.add(kind);
  }
}

function parseJsonSafe(text) {
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
}

async function parseResponse(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json();
  }
  const text = await response.text();
  if (!text) {
    return {};
  }
  return parseJsonSafe(text);
}

async function apiFetch(endpoint, options = {}) {
  const headers = new Headers(options.headers || {});
  const key = getAdminKey();
  if (key) {
    headers.set("Authorization", `Bearer ${key}`);
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 15000);
  let response;
  try {
    response = await fetch(API_BASE + endpoint, {
      ...options,
      headers,
      cache: "no-store",
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeoutId);
  }

  if (response.status === 401) {
    setAdminAuthError("Invalid or missing admin key.");
    setAuthState("fail");
  } else if (response.ok) {
    setAdminAuthError("");
  }

  const data = await parseResponse(response);
  return { ok: response.ok, status: response.status, data, response };
}

function formatJson(data) {
  return JSON.stringify(data, null, 2);
}

function formatTimestamp(value) {
  if (!value) {
    return "";
  }
  const ms = Number(value) > 1e12 ? Number(value) : Number(value) * 1000;
  if (!Number.isFinite(ms)) {
    return String(value);
  }
  return new Date(ms).toLocaleString();
}

function getHealthDependencies(healthData) {
  const dependencies = healthData && typeof healthData === "object" ? healthData.dependencies : null;
  return dependencies && typeof dependencies === "object" ? dependencies : {};
}

async function loadHealth() {
  const chipDot = select("header-health-dot");
  const chipText = select("header-health-text");
  const statusPills = select("overview-status-pills");

  let result;
  try {
    result = await apiFetch("/health", { method: "GET" });
  } catch {
    if (chipDot) chipDot.classList.add("bad");
    if (chipText) chipText.textContent = "unreachable";
    if (statusPills) statusPills.textContent = "Health check failed (network error or timeout).";
    return;
  }

  if (!result.ok) {
    if (chipDot) {
      chipDot.classList.add("bad");
    }
    if (chipText) {
      chipText.textContent = "unhealthy";
    }
    if (statusPills) {
      statusPills.textContent = "Unable to load health status.";
    }
    return;
  }

  const status = result.data.status || "unknown";
  if (chipDot) {
    chipDot.classList.remove("ok", "bad");
    chipDot.classList.add(status === "ok" ? "ok" : "bad");
  }
  if (chipText) {
    chipText.textContent = status === "ok" ? "healthy" : status;
  }

  if (!statusPills) {
    return;
  }

  const dependencies = getHealthDependencies(result.data);
  statusPills.innerHTML = "";
  const names = Object.keys(dependencies);
  if (!names.length) {
    statusPills.textContent = "No dependency status returned.";
    return;
  }

  for (const name of names) {
    const value = String(dependencies[name]);
    const ok = value === "ok";
    const pill = document.createElement("span");
    pill.className = `dependency-pill ${ok ? "ok" : "bad"}`;
    pill.textContent = `${name}: ${ok ? "OK" : "ERROR"}`;
    if (!ok) {
      pill.title = value;
    }
    statusPills.appendChild(pill);
  }

  // Auto-populate RAJEE endpoint from server config if the field is empty
  const config = result.data.config || {};
  if (config.rajee_endpoint) {
    const probeEndpoint = select("probe-endpoint");
    if (probeEndpoint && !probeEndpoint.value.trim()) {
      probeEndpoint.value = config.rajee_endpoint;
    }
  }
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

async function loadAuthorityView() {
  const jwksEl = select("authority-jwks");
  const principalsBody = select("authority-principals-body");
  const policiesBody = select("authority-policies-body");

  if (jwksEl) {
    jwksEl.textContent = "Loading JWKS...";
  }
  if (principalsBody) {
    principalsBody.innerHTML = "<tr><td colspan=\"3\">Loading principals...</td></tr>";
  }
  if (policiesBody) {
    policiesBody.innerHTML = "<tr><td colspan=\"3\">Loading policies...</td></tr>";
  }

  const [jwks, principals, policies] = await Promise.all([
    apiFetch("/.well-known/jwks.json", { method: "GET" }),
    apiFetch("/principals", { method: "GET" }),
    apiFetch("/policies", { method: "GET" }),
  ]);

  if (jwksEl) {
    jwksEl.textContent = jwks.ok ? formatJson(jwks.data) : formatJson(jwks.data || { error: "Failed" });
  }

  if (principalsBody) {
    principalsBody.innerHTML = "";
    const items = principals.ok ? principals.data.principals || [] : [];
    if (!items.length) {
      principalsBody.innerHTML = "<tr><td colspan=\"3\">No principals found.</td></tr>";
    } else {
      for (const item of items) {
        const tr = document.createElement("tr");
        const principal = item.principal || "(unknown)";
        const scopes = Array.isArray(item.scopes) ? item.scopes : [];
        const dzProject = item.datazone_project_id || "-";
        tr.innerHTML = `<td>${escapeHtml(principal)}</td><td>${scopes.length}</td><td>${escapeHtml(dzProject)}</td>`;
        principalsBody.appendChild(tr);
      }
    }
  }

  if (policiesBody) {
    policiesBody.innerHTML = "";
    const items = policies.ok ? policies.data.policies || [] : [];
    if (!items.length) {
      policiesBody.innerHTML = "<tr><td colspan=\"3\">No listings found.</td></tr>";
    } else {
      for (const policy of items) {
        const tr = document.createElement("tr");
        const policyId = policy.policyId || "(unknown)";
        const name = policy.name || "-";
        const ownerProjectId = policy.owningProjectId || "-";
        tr.innerHTML = `<td>${escapeHtml(policyId)}</td><td>${escapeHtml(name)}</td><td>${escapeHtml(ownerProjectId)}</td>`;
        policiesBody.appendChild(tr);
      }
    }
  }
}

function jwtDecodePart(part) {
  const normalized = part.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
  return JSON.parse(atob(padded));
}

function decodeJwt(token) {
  const parts = token.split(".");
  if (parts.length !== 3) {
    throw new Error("Invalid token format");
  }
  return {
    header: jwtDecodePart(parts[0]),
    payload: jwtDecodePart(parts[1]),
    signature: parts[2],
  };
}

function claimNote(key) {
  const notes = {
    sub: "who this token speaks for",
    aud: "which service will accept it",
    scopes: "compiled permissions (no policy eval needed)",
    iat: "issued at",
    exp: "expires at (TTL bounds exposure)",
  };
  return notes[key] || "claim value";
}

function renderAnnotatedClaims(containerId, payload) {
  const container = select(containerId);
  if (!container) {
    return;
  }
  container.innerHTML = "";

  if (!payload || typeof payload !== "object") {
    container.textContent = "No claims available.";
    return;
  }

  for (const [key, rawValue] of Object.entries(payload)) {
    const row = document.createElement("div");
    row.className = "claim-row";

    const keyEl = document.createElement("div");
    keyEl.className = "claim-key";
    keyEl.textContent = key;

    const valueEl = document.createElement("div");
    valueEl.className = "claim-value";
    const value = typeof rawValue === "object" ? formatJson(rawValue) : String(rawValue);
    valueEl.textContent = value;

    const noteEl = document.createElement("div");
    noteEl.className = "claim-note";
    noteEl.textContent = `-> ${claimNote(key)}`;

    row.appendChild(keyEl);
    row.appendChild(valueEl);
    row.appendChild(noteEl);
    container.appendChild(row);
  }
}

function evaluateTokenStatus(payload, expectedAudience) {
  const now = Math.floor(Date.now() / 1000);
  if (typeof payload.exp === "number" && payload.exp <= now) {
    return { kind: "warn", text: "EXPIRED" };
  }

  if (expectedAudience) {
    const aud = payload.aud;
    const audValues = Array.isArray(aud) ? aud.map(String) : aud ? [String(aud)] : [];
    if (!audValues.includes(expectedAudience)) {
      return { kind: "warn", text: "WRONG AUDIENCE" };
    }
  }

  return { kind: "valid", text: "VALID" };
}

function renderProbeOutput(data) {
  const out = select("probe-output");
  if (!out) {
    return;
  }

  const lines = [];
  lines.push("token minted -> request sent -> response received");
  lines.push("");
  lines.push(`principal: ${data.principal || ""}`);
  lines.push(`endpoint: ${data.endpoint || ""}`);
  lines.push(`usl: ${data.usl || ""}`);
  lines.push("");
  lines.push(`response status: ${data.status_code ?? "(none)"}`);
  lines.push(`reachable: ${String(Boolean(data.rajee_reachable))}`);

  const headers = data.headers && typeof data.headers === "object" ? data.headers : {};
  lines.push("");
  lines.push("response headers:");
  if (!Object.keys(headers).length) {
    lines.push("  (none)");
  } else {
    for (const [name, value] of Object.entries(headers)) {
      lines.push(`  ${name}: ${value}`);
    }
  }

  const diagnostics =
    data.diagnostic_headers && typeof data.diagnostic_headers === "object"
      ? data.diagnostic_headers
      : {};
  lines.push("");
  lines.push("x-raja-* diagnostics:");
  if (!Object.keys(diagnostics).length) {
    lines.push("  (none)");
  } else {
    for (const [name, value] of Object.entries(diagnostics)) {
      lines.push(`  ${name}: ${value}`);
    }
  }

  if (data.error) {
    lines.push("");
    lines.push(`error: ${data.error}`);
  }

  out.textContent = lines.join("\n");
}

function rotationStepsForStatus(status, phase, error) {
  const succeeded = status === "SUCCEEDED";
  const failed = status === "FAILED";

  const checkpoints = [
    { label: "Create new secret version", done: succeeded || phase !== "starting" },
    { label: "Update Lambda env vars", done: succeeded || phase !== "starting" },
    { label: "Flush warm containers", done: succeeded || phase !== "starting" },
    { label: "Run completion probes", done: succeeded, active: !succeeded && !failed },
    {
      label: `Status: ${status || "PENDING"}${error ? ` (${error})` : ""}`,
      done: succeeded,
      active: !succeeded && !failed,
      failed,
    },
  ];

  return checkpoints;
}

function renderRotationTimeline(statusPayload) {
  const timeline = select("rotate-timeline");
  if (!timeline) {
    return;
  }

  timeline.innerHTML = "";
  const steps = rotationStepsForStatus(
    statusPayload.status,
    statusPayload.phase,
    statusPayload.error || ""
  );

  for (const step of steps) {
    const li = document.createElement("li");
    const icon = step.done ? "\u2713" : step.failed ? "\u2717" : step.active ? "\u27f3" : "\u2022";
    li.textContent = `${icon} ${step.label}`;
    timeline.appendChild(li);
  }
}

function buildAuditQuery(params) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== "" && value !== null && value !== undefined) {
      query.set(key, String(value));
    }
  }
  return query.toString();
}

function routeTo(hash) {
  const view = VIEW_IDS.includes(hash) ? hash : DEFAULT_VIEW;

  for (const id of VIEW_IDS) {
    const section = select(`view-${id}`);
    if (section) {
      section.hidden = id !== view;
    }
  }

  const nav = select("side-nav");
  if (nav) {
    const links = nav.querySelectorAll("a[data-view]");
    for (const link of links) {
      const active = link.getAttribute("data-view") === view;
      link.classList.toggle("active", active);
    }
  }

  if (!state.loadedViews.has(view)) {
    state.loadedViews.add(view);
    if (view === "overview") {
      loadHealth();
    }
    if (view === "authority") {
      loadAuthorityView();
    }
    if (view === "incident") {
      loadIncidentPrincipals();
    }
    if (view === "failures") {
      loadFailureTests();
    }
    if (view === "audit") {
      loadAudit();
    }
  }
}

function currentHashView() {
  const value = window.location.hash.replace("#", "").trim();
  return VIEW_IDS.includes(value) ? value : DEFAULT_VIEW;
}

async function loadIncidentPrincipals() {
  const selector = select("incident-principal");
  if (!selector) {
    return;
  }

  selector.innerHTML = "<option>Loading...</option>";
  const result = await apiFetch("/principals", { method: "GET" });
  selector.innerHTML = "";

  if (!result.ok) {
    selector.innerHTML = "<option value=''>Unable to load principals</option>";
    return;
  }

  const principals = result.data.principals || [];
  if (!principals.length) {
    selector.innerHTML = "<option value=''>No principals</option>";
    return;
  }

  for (const item of principals) {
    const opt = document.createElement("option");
    opt.value = item.principal;
    opt.textContent = item.principal;
    selector.appendChild(opt);
  }
}

async function loadAudit() {
  const body = select("audit-table-body");
  if (!body) {
    return;
  }
  body.innerHTML = "<tr><td colspan=\"5\">Loading...</td></tr>";

  const result = await apiFetch("/audit", { method: "GET" });
  if (!result.ok) {
    body.innerHTML = `<tr><td colspan=\"5\">Failed to load audit: ${escapeHtml(formatJson(result.data))}</td></tr>`;
    return;
  }

  renderAuditRows(result.data.entries || []);
}

function renderAuditRows(entries) {
  const body = select("audit-table-body");
  if (!body) {
    return;
  }
  body.innerHTML = "";

  if (!entries.length) {
    body.innerHTML = "<tr><td colspan=\"5\">No audit entries found.</td></tr>";
    return;
  }

  for (const entry of entries) {
    const tr = document.createElement("tr");
    const time = formatTimestamp(entry.timestamp || entry.time || entry.created_at);
    tr.innerHTML = `
      <td>${escapeHtml(time || "-")}</td>
      <td>${escapeHtml(entry.principal || "-")}</td>
      <td>${escapeHtml(entry.action || "-")}</td>
      <td>${escapeHtml(entry.resource || "-")}</td>
      <td>${escapeHtml(entry.decision || "-")}</td>
    `;
    body.appendChild(tr);
  }
}

function setupEvents() {
  const adminKeyInput = select("admin-key");
  if (adminKeyInput) {
    adminKeyInput.value = getAdminKey();
    adminKeyInput.addEventListener("input", () => {
      setAdminKey(adminKeyInput.value);
      setAdminAuthError("");
      if (!adminKeyInput.value) {
        setAuthState("unknown");
      } else {
        schedulePingAuth();
      }
    });
  }

  const authorityRefresh = select("authority-refresh");
  if (authorityRefresh) {
    authorityRefresh.addEventListener("click", loadAuthorityView);
  }

  const tokenForm = select("token-form");
  if (tokenForm) {
    tokenForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const principal = select("token-principal")?.value?.trim() || "User::demo";
      const tokenType = select("token-type")?.value || "raja";

      const result = await apiFetch("/token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ principal, token_type: tokenType }),
      });

      if (!result.ok) {
        setStatusBanner("token-mint-status", `Mint failed (${result.status}).`, "invalid");
        renderAnnotatedClaims("token-claims-annotated", result.data || {});
        return;
      }

      const token = result.data.token || "";
      const tokenOut = select("token-output");
      const verifyInput = select("verify-token");
      if (tokenOut) {
        tokenOut.value = token;
      }
      if (verifyInput) {
        verifyInput.value = token;
      }

      try {
        const decoded = decodeJwt(token);
        renderAnnotatedClaims("token-claims-annotated", decoded.payload);
      } catch {
        renderAnnotatedClaims("token-claims-annotated", { error: "unable to decode token" });
      }

      setStatusBanner("token-mint-status", "Token minted.", "valid");
    });
  }

  const verifyForm = select("verify-form");
  if (verifyForm) {
    verifyForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const token = select("verify-token")?.value?.trim() || "";
      const audience = select("verify-audience")?.value?.trim() || "";

      if (!token) {
        setStatusBanner("verify-status", "INVALID", "invalid");
        renderAnnotatedClaims("verify-claims-annotated", { error: "token required" });
        return;
      }

      try {
        const decoded = decodeJwt(token);
        renderAnnotatedClaims("verify-claims-annotated", decoded.payload);
        const status = evaluateTokenStatus(decoded.payload, audience);
        setStatusBanner("verify-status", status.text, status.kind);
      } catch (error) {
        setStatusBanner("verify-status", "INVALID", "invalid");
        renderAnnotatedClaims("verify-claims-annotated", { error: String(error) });
      }
    });
  }

  const probeHealth = select("probe-health");
  if (probeHealth) {
    probeHealth.addEventListener("click", async () => {
      const endpoint = select("probe-endpoint")?.value?.trim() || "";
      const result = await apiFetch(`/probe/rajee/health?endpoint=${encodeURIComponent(endpoint)}`, {
        method: "GET",
      });
      if (!result.ok || !result.data.reachable) {
        setStatusBanner("probe-status", "RAJEE unreachable", "invalid");
      } else if (result.data.ready) {
        setStatusBanner("probe-status", "RAJEE reachable and ready", "valid");
      } else {
        setStatusBanner("probe-status", `RAJEE reachable (status ${result.data.status_code})`, "warn");
      }
      const out = select("probe-output");
      if (out) {
        out.textContent = formatJson(result.data);
      }
    });
  }

  const probeForm = select("probe-form");
  if (probeForm) {
    probeForm.addEventListener("submit", async (event) => {
      event.preventDefault();

      const endpoint = select("probe-endpoint")?.value?.trim() || "";
      const principal = select("probe-principal")?.value?.trim() || "";
      const usl = select("probe-usl")?.value?.trim() || "";

      const health = await apiFetch(`/probe/rajee/health?endpoint=${encodeURIComponent(endpoint)}`, {
        method: "GET",
      });

      if (!health.ok || !health.data.reachable) {
        setStatusBanner("probe-status", "RAJEE health check failed. Probe not attempted.", "invalid");
        const out = select("probe-output");
        if (out) {
          out.textContent = formatJson(health.data || {});
        }
        return;
      }

      const result = await apiFetch("/probe/rajee", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ principal, usl, rajee_endpoint: endpoint }),
      });

      if (!result.ok) {
        setStatusBanner("probe-status", `Probe failed (${result.status})`, "invalid");
        const out = select("probe-output");
        if (out) {
          out.textContent = formatJson(result.data || {});
        }
        return;
      }

      setStatusBanner("probe-status", `Probe completed with HTTP ${result.data.status_code}`, "valid");
      renderProbeOutput(result.data);
    });
  }

  const refreshPrincipalsButton = select("incident-refresh-principals");
  if (refreshPrincipalsButton) {
    refreshPrincipalsButton.addEventListener("click", loadIncidentPrincipals);
  }

  const deletePrincipalButton = select("incident-delete-principal");
  if (deletePrincipalButton) {
    deletePrincipalButton.addEventListener("click", async () => {
      const principal = select("incident-principal")?.value || "";
      if (!principal) {
        return;
      }
      if (!window.confirm(`Delete principal ${principal}?`)) {
        return;
      }

      const result = await apiFetch(`/principals/${encodeURIComponent(principal)}`, {
        method: "DELETE",
      });
      const resultEl = select("incident-soft-result");
      if (!result.ok) {
        if (resultEl) {
          resultEl.textContent = formatJson(result.data);
        }
        return;
      }

      if (resultEl) {
        resultEl.textContent = `${principal} deleted.\nExisting tokens remain valid for up to 3600s.\nNew issuance: BLOCKED.`;
      }
      await loadIncidentPrincipals();
    });
  }

  const rotateButton = select("incident-rotate-secret");
  if (rotateButton) {
    rotateButton.addEventListener("click", async () => {
      const ok = window.confirm(
        "This invalidates all currently issued tokens globally. Continue with secret rotation?"
      );
      if (!ok) {
        return;
      }

      const result = await apiFetch("/admin/rotate-secret", { method: "POST" });
      const output = select("incident-hard-result");

      if (!result.ok) {
        if (output) {
          output.textContent = formatJson(result.data);
        }
        renderRotationTimeline({ status: "FAILED", phase: "failed", error: "rotation request failed" });
        return;
      }

      state.rotateOperationId = result.data.operation_id;
      if (output) {
        output.textContent = `operation_id: ${state.rotateOperationId}`;
      }

      await pollRotationStatus();
    });
  }

  const auditForm = select("audit-filter-form");
  if (auditForm) {
    auditForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const query = buildAuditQuery({
        principal: select("audit-principal")?.value?.trim() || "",
        action: select("audit-action")?.value?.trim() || "",
        resource: select("audit-resource")?.value?.trim() || "",
        start_time: select("audit-start-time")?.value || "",
        end_time: select("audit-end-time")?.value || "",
      });

      const endpoint = query ? `/audit?${query}` : "/audit";
      const result = await apiFetch(endpoint, { method: "GET" });
      if (!result.ok) {
        renderAuditRows([]);
        return;
      }
      renderAuditRows(result.data.entries || []);
    });
  }

  window.addEventListener("hashchange", () => routeTo(currentHashView()));
}

async function pollRotationStatus() {
  if (!state.rotateOperationId) {
    return;
  }

  const result = await apiFetch(
    `/admin/rotate-secret/${encodeURIComponent(state.rotateOperationId)}`,
    { method: "GET" }
  );

  if (!result.ok) {
    renderRotationTimeline({ status: "FAILED", phase: "failed", error: "status polling failed" });
    return;
  }

  renderRotationTimeline(result.data);
  const output = select("incident-hard-result");
  if (output) {
    output.textContent = formatJson(result.data);
  }

  if (state.rotatePollTimer) {
    window.clearTimeout(state.rotatePollTimer);
    state.rotatePollTimer = null;
  }

  if (result.data.status === "SUCCEEDED" || result.data.status === "FAILED") {
    return;
  }

  state.rotatePollTimer = window.setTimeout(() => {
    pollRotationStatus();
  }, 2000);
}

const failureState = {
  tests: [],
  testsById: {},
  categories: [],
  selectedCategory: null,
  runs: {},
  busyTests: new Set(),
  busyCategory: false,
  lastCategoryError: null,
};

function getTestsForSelectedCategory() {
  if (!failureState.selectedCategory) {
    return [];
  }
  return failureState.tests.filter((test) => test.category === failureState.selectedCategory);
}

function updateCategoryRunButton() {
  const button = select("failure-run-category");
  if (!button) {
    return;
  }
  const ready = !!failureState.selectedCategory && !failureState.busyCategory;
  button.disabled = !ready;
  button.textContent = failureState.busyCategory ? "Running..." : "Run category";
}

function updateExportButtonState() {
  const button = select("failure-export-results");
  if (!button) {
    return;
  }
  button.disabled = Object.keys(failureState.runs).length === 0;
}

function renderFailureCategories() {
  const container = select("failure-category-selector");
  if (!container) {
    return;
  }
  container.innerHTML = "";

  if (!failureState.categories.length) {
    container.textContent = "No failure test categories defined.";
    return;
  }

  for (const category of failureState.categories) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "failure-category-pill";
    if (failureState.selectedCategory === category.id) {
      button.classList.add("active");
    }
    button.textContent = `${category.label} (${category.tests.length})`;
    button.addEventListener("click", () => {
      failureState.selectedCategory = category.id;
      renderFailureCategories();
      renderFailureTestList();
      renderFailureSummary();
      updateCategoryRunButton();
    });
    container.appendChild(button);
  }
}

function createFailureTestCard(test) {
  const card = document.createElement("article");
  card.className = "failure-test-card";

  const header = document.createElement("header");

  const badge = document.createElement("span");
  badge.className = `failure-priority-badge ${test.priority}`;
  badge.textContent = test.priority;

  const title = document.createElement("h3");
  title.textContent = `${test.id} ${test.title}`;

  const runButton = document.createElement("button");
  runButton.type = "button";
  runButton.className = "secondary";
  runButton.textContent = failureState.busyTests.has(test.id) ? "Running..." : "Run test";
  runButton.disabled = failureState.busyTests.has(test.id);
  runButton.addEventListener("click", () => runFailureTest(test.id));

  header.appendChild(badge);
  header.appendChild(title);
  header.appendChild(runButton);

  const description = document.createElement("p");
  description.className = "failure-test-desc";
  description.textContent = test.description;

  const expected = document.createElement("div");
  expected.className = "failure-expected";
  expected.innerHTML = `<strong>Expected:</strong> ${escapeHtml(test.expected_summary)}`;

  const run = failureState.runs[test.id];
  const actual = document.createElement("div");
  actual.className = "failure-actual";
  actual.innerHTML = `<strong>Actual:</strong> ${escapeHtml(run ? run.actual : "Not run yet")}`;

  const status = document.createElement("span");
  status.className = "failure-status-pill";
  if (run) {
    status.classList.add(run.status);
    status.textContent = run.status;
  } else {
    status.textContent = "PENDING";
  }

  card.appendChild(header);
  card.appendChild(description);
  card.appendChild(expected);
  card.appendChild(actual);
  card.appendChild(status);

  return card;
}

function renderFailureTestList() {
  const list = select("failure-test-list");
  if (!list) {
    return;
  }
  list.innerHTML = "";
  const tests = getTestsForSelectedCategory();
  if (!tests.length) {
    list.textContent = "No tests defined for this category.";
    return;
  }
  for (const test of tests) {
    list.appendChild(createFailureTestCard(test));
  }
}

function renderFailureSummary() {
  const summary = select("failure-run-summary");
  if (!summary) {
    return;
  }
  summary.innerHTML = "";

  const tests = getTestsForSelectedCategory();
  if (!tests.length) {
    summary.textContent = "Select a category to inspect results.";
    return;
  }

  const counts = { pass: 0, fail: 0, error: 0, pending: 0 };
  for (const test of tests) {
    const run = failureState.runs[test.id];
    if (!run) {
      counts.pending += 1;
    } else if (run.status === "PASS") {
      counts.pass += 1;
    } else if (run.status === "FAIL") {
      counts.fail += 1;
    } else {
      counts.error += 1;
    }
  }

  summary.innerHTML = `<strong>Summary:</strong> ${counts.pass} pass · ${counts.fail} fail · ${counts.error} errors · ${counts.pending} pending`;
  if (failureState.lastCategoryError) {
    const error = document.createElement("div");
    error.innerHTML = `<strong>Error:</strong> ${escapeHtml(failureState.lastCategoryError)}`;
    summary.appendChild(error);
  }
}

async function loadFailureTests() {
  const categories = select("failure-category-selector");
  const tests = select("failure-test-list");
  if (categories) {
    categories.textContent = "Loading failure test categories...";
  }
  if (tests) {
    tests.textContent = "Loading failure tests...";
  }

  const result = await apiFetch("/api/failure-tests/", { method: "GET" });
  if (!result.ok) {
    if (categories) {
      categories.textContent = "Unable to load failure tests.";
    }
    if (tests) {
      tests.textContent = "Failure test list unavailable.";
    }
    failureState.lastCategoryError = formatJson(result.data);
    renderFailureSummary();
    return;
  }

  failureState.tests = result.data.tests || [];
  failureState.testsById = Object.fromEntries(failureState.tests.map((test) => [test.id, test]));
  failureState.categories = result.data.categories || [];
  failureState.selectedCategory = failureState.categories[0]?.id || null;
  failureState.lastCategoryError = null;

  renderFailureCategories();
  renderFailureTestList();
  renderFailureSummary();
  updateCategoryRunButton();
  updateExportButtonState();
}

async function runFailureTest(testId) {
  if (failureState.busyTests.has(testId)) {
    return;
  }

  failureState.busyTests.add(testId);
  renderFailureTestList();
  updateCategoryRunButton();

  const result = await apiFetch(`/api/failure-tests/${encodeURIComponent(testId)}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });

  failureState.busyTests.delete(testId);

  if (!result.ok) {
    failureState.runs[testId] = {
      test_id: testId,
      status: "ERROR",
      expected: failureState.testsById[testId]?.expected_summary || "Expected failure",
      actual: formatJson(result.data),
      timestamp: Date.now() / 1000,
    };
  } else {
    failureState.runs[testId] = result.data;
  }

  renderFailureTestList();
  renderFailureSummary();
  updateCategoryRunButton();
  updateExportButtonState();
}

async function runFailureCategory() {
  if (!failureState.selectedCategory || failureState.busyCategory) {
    return;
  }
  failureState.busyCategory = true;
  updateCategoryRunButton();

  const result = await apiFetch(
    `/api/failure-tests/categories/${encodeURIComponent(failureState.selectedCategory)}/run`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    }
  );

  failureState.busyCategory = false;
  if (!result.ok) {
    failureState.lastCategoryError = formatJson(result.data);
  } else {
    for (const run of result.data.results || []) {
      failureState.runs[run.test_id] = run;
    }
    failureState.lastCategoryError = null;
  }

  renderFailureTestList();
  renderFailureSummary();
  updateCategoryRunButton();
  updateExportButtonState();
}

function exportFailureResults() {
  const payload = {
    timestamp: Date.now(),
    runs: failureState.runs,
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const anchor = document.createElement("a");
  anchor.href = URL.createObjectURL(blob);
  anchor.download = `failure-tests-${Date.now()}.json`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

function boot() {
  setupEvents();

  const runCategoryButton = select("failure-run-category");
  if (runCategoryButton) {
    runCategoryButton.addEventListener("click", runFailureCategory);
  }

  const exportButton = select("failure-export-results");
  if (exportButton) {
    exportButton.addEventListener("click", exportFailureResults);
  }

  const hash = currentHashView();
  routeTo(hash);
  if (!window.location.hash) {
    window.location.hash = `#${hash}`;
  }

  loadHealth();
  if (getAdminKey()) {
    pingAuth();
  }
}

boot();
