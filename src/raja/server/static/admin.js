const select = (id) => document.getElementById(id);

function buildUrl(endpoint) {
  const basePath = window.location.pathname.endsWith("/")
    ? window.location.pathname
    : `${window.location.pathname}/`;
  return new URL(endpoint, `${window.location.origin}${basePath}`);
}

function writeJson(id, data) {
  select(id).textContent = JSON.stringify(data, null, 2);
}

function setInputDefault(id, value) {
  const el = select(id);
  if (!el.value) {
    el.value = value;
  }
}

async function refreshConfig() {
  try {
    const response = await fetch(buildUrl("s3-harness/config"));
    const data = await response.json();
    select("issuer").textContent = data.issuer;
    select("audience").textContent = data.audience;
    writeJson("jwks", data.jwks);
    setInputDefault("mint-audience", data.audience);
    setInputDefault("verify-aud", data.audience);
    setInputDefault("enforce-aud", data.audience);
  } catch (err) {
    select("issuer").textContent = "Unavailable";
    select("audience").textContent = "Unavailable";
    select("jwks").textContent = String(err);
  }
}

async function postJson(endpoint, payload) {
  const response = await fetch(buildUrl(endpoint), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  return { ok: response.ok, data };
}

select("refresh-config").addEventListener("click", refreshConfig);

select("mint-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    subject: select("mint-subject").value.trim() || "User::demo",
    audience: select("mint-audience").value.trim() || "raja-s3",
    action: select("mint-action").value,
    bucket: select("mint-bucket").value.trim() || "demo-bucket",
  };
  const key = select("mint-key").value.trim();
  const prefix = select("mint-prefix").value.trim();
  if (key && prefix) {
    writeJson("mint-claims", { error: "Provide a key OR prefix, not both." });
    return;
  }
  if (!key && !prefix) {
    writeJson("mint-claims", { error: "Provide a key or prefix." });
    return;
  }
  if (key) {
    payload.key = key;
  } else {
    payload.prefix = prefix;
  }
  const ttl = Number(select("mint-ttl").value);
  if (ttl) {
    payload.ttl = ttl;
  }
  const result = await postJson("s3-harness/mint", payload);
  if (!result.ok) {
    writeJson("mint-claims", result.data);
    return;
  }
  select("mint-token").value = result.data.token;
  select("verify-token").value = result.data.token;
  select("enforce-token").value = result.data.token;
  writeJson("mint-claims", result.data);
});

select("verify-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    token: select("verify-token").value.trim(),
    audience: select("verify-aud").value.trim() || undefined,
  };
  const result = await postJson("s3-harness/verify", payload);
  writeJson("verify-output", result.data);
});

select("enforce-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    token: select("enforce-token").value.trim(),
    audience: select("enforce-aud").value.trim() || undefined,
    action: select("enforce-action").value,
    bucket: select("enforce-bucket").value.trim(),
    key: select("enforce-key").value.trim(),
  };
  const result = await postJson("s3-harness/enforce", payload);
  writeJson("enforce-output", result.data);
});

select("load-control-plane").addEventListener("click", async () => {
  const targets = [
    { endpoint: "principals", id: "principals" },
    { endpoint: "policies", id: "policies" },
    { endpoint: "audit", id: "audit" },
  ];
  for (const target of targets) {
    try {
      const response = await fetch(buildUrl(target.endpoint));
      const data = await response.json();
      writeJson(target.id, data);
    } catch (err) {
      select(target.id).textContent = String(err);
    }
  }
});

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

const failureCategorySelector = select("failure-category-selector");
const failureTestList = select("failure-test-list");
const failureRunSummary = select("failure-run-summary");
const failureRunCategoryButton = select("failure-run-category");
const failureExportButton = select("failure-export-results");

async function loadFailureTests() {
  try {
    const response = await fetch(buildUrl("api/failure-tests"));
    if (!response.ok) {
      throw new Error(`Failed to load failure tests (${response.status})`);
    }
    const data = await response.json();
    failureState.tests = data.tests || [];
    failureState.testsById = failureState.tests.reduce((acc, test) => {
      acc[test.id] = test;
      return acc;
    }, {});
    failureState.categories = data.categories || [];
    failureState.selectedCategory = failureState.categories[0]?.id || null;
    renderFailureCategories();
    renderFailureTestList();
    renderFailureSummary();
    updateCategoryRunButton();
    failureState.lastCategoryError = null;
  } catch (err) {
    const message = `Unable to load failure tests: ${err.message || err}`;
    failureCategorySelector.textContent = message;
    failureTestList.textContent = "Failure test list unavailable.";
    failureRunSummary.textContent = "";
    failureState.lastCategoryError = message;
    console.error(message);
  }
}

function renderFailureCategories() {
  failureCategorySelector.innerHTML = "";
  if (!failureState.categories.length) {
    failureCategorySelector.textContent = "No failure test categories defined.";
    return;
  }
  for (const category of failureState.categories) {
    const pill = document.createElement("button");
    pill.type = "button";
    pill.className = "failure-category-pill";
    if (failureState.selectedCategory === category.id) {
      pill.classList.add("active");
    }
    pill.textContent = `${category.label} (${category.tests.length})`;
    pill.addEventListener("click", () => {
      failureState.selectedCategory = category.id;
      renderFailureCategories();
      renderFailureTestList();
      renderFailureSummary();
      updateCategoryRunButton();
    });
    failureCategorySelector.appendChild(pill);
  }
}

function getTestsForSelectedCategory() {
  if (!failureState.selectedCategory) {
    return [];
  }
  return failureState.tests.filter((test) => test.category === failureState.selectedCategory);
}

function renderFailureTestList() {
  failureTestList.innerHTML = "";
  const tests = getTestsForSelectedCategory();
  if (!tests.length) {
    failureTestList.textContent = "No tests defined for this category.";
    return;
  }
  for (const test of tests) {
    failureTestList.appendChild(createFailureTestCard(test));
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
  runButton.textContent = failureState.busyTests.has(test.id) ? "Running…" : "Run test";
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
  const expectedLabel = document.createElement("strong");
  expectedLabel.textContent = "Expected: ";
  expected.appendChild(expectedLabel);
  expected.appendChild(document.createTextNode(test.expected_summary));

  const actual = document.createElement("div");
  actual.className = "failure-actual";
  const actualLabel = document.createElement("strong");
  actualLabel.textContent = "Actual: ";
  actual.appendChild(actualLabel);
  const lastRun = failureState.runs[test.id];
  actual.appendChild(
    document.createTextNode(lastRun ? lastRun.actual : "Not run yet")
  );

  const status = document.createElement("span");
  status.className = "failure-status-pill";
  if (lastRun) {
    status.classList.add(lastRun.status);
    status.textContent = lastRun.status;
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

function renderFailureSummary() {
  failureRunSummary.innerHTML = "";
  if (!failureState.selectedCategory) {
    failureRunSummary.textContent = "Select a category to inspect results.";
    return;
  }
  const tests = getTestsForSelectedCategory();
  if (!tests.length) {
    failureRunSummary.textContent = "No tests are configured for this category.";
    return;
  }
  const summary = tests.reduce(
    (acc, test) => {
      const run = failureState.runs[test.id];
      if (!run) {
        acc.pending += 1;
      } else if (run.status === "PASS") {
        acc.pass += 1;
      } else if (run.status === "FAIL") {
        acc.fail += 1;
      } else {
        acc.error += 1;
      }
      return acc;
    },
    { pass: 0, fail: 0, error: 0, pending: 0 }
  );
  const summaryLine = document.createElement("div");
  summaryLine.innerHTML = `<strong>Summary:</strong> ${summary.pass} pass · ${summary.fail} fail · ${summary.error} errors · ${summary.pending} pending`;
  failureRunSummary.appendChild(summaryLine);
  const lastRunTimes = tests
    .map((test) => failureState.runs[test.id])
    .filter(Boolean)
    .map((run) => new Date(run.timestamp * 1000).toLocaleTimeString());
  if (lastRunTimes.length) {
    const historyLine = document.createElement("div");
    historyLine.textContent = `Last run at ${lastRunTimes.slice(-1)[0]}`;
    failureRunSummary.appendChild(historyLine);
  }
  if (failureState.lastCategoryError) {
    const errorLine = document.createElement("div");
    errorLine.innerHTML = `<strong>Error:</strong> ${failureState.lastCategoryError}`;
    failureRunSummary.appendChild(errorLine);
  }
}

async function runFailureTest(testId) {
  if (failureState.busyTests.has(testId)) {
    return;
  }
  failureState.busyTests.add(testId);
  renderFailureTestList();
  updateCategoryRunButton();
  const result = await postJson(`api/failure-tests/${testId}/run`, {});
  failureState.busyTests.delete(testId);
  if (!result.ok) {
    const fallback = {
      run_id: "",
      test_id: testId,
      status: "ERROR",
      expected: failureState.testsById[testId]?.expected_summary || "Expected failure",
      actual: JSON.stringify(result.data),
      details: result.data,
      timestamp: Date.now() / 1000,
    };
    failureState.runs[testId] = fallback;
  } else {
    failureState.runs[testId] = result.data;
  }
  renderFailureTestList();
  renderFailureSummary();
  updateExportButtonState();
  updateCategoryRunButton();
  failureState.lastCategoryError = null;
}

async function runFailureCategory() {
  if (!failureState.selectedCategory || failureState.busyCategory) {
    return;
  }
  failureState.busyCategory = true;
  updateCategoryRunButton();
  const endpoint = `api/failure-tests/categories/${failureState.selectedCategory}/run`;
  const result = await postJson(endpoint, {});
  failureState.busyCategory = false;
  if (!result.ok) {
    failureState.lastCategoryError = JSON.stringify(result.data);
    const fallback = {
      run_id: "",
      test_id: failureState.selectedCategory,
      status: "ERROR",
      expected: "Batch execution",
      actual: JSON.stringify(result.data),
      details: result.data,
      timestamp: Date.now() / 1000,
    };
    failureState.runs[failureState.selectedCategory] = fallback;
  } else {
    result.data.results.forEach((run) => {
      failureState.runs[run.test_id] = run;
    });
    failureState.lastCategoryError = null;
  }
  renderFailureTestList();
  renderFailureSummary();
  updateExportButtonState();
  updateCategoryRunButton();
}

function updateCategoryRunButton() {
  const ready = !!failureState.selectedCategory && !failureState.busyCategory;
  failureRunCategoryButton.disabled = !ready;
  failureRunCategoryButton.textContent = failureState.busyCategory ? "Running…" : "Run category";
}

function updateExportButtonState() {
  const hasRuns = Object.keys(failureState.runs).length > 0;
  failureExportButton.disabled = !hasRuns;
}

failureRunCategoryButton.addEventListener("click", runFailureCategory);
failureExportButton.addEventListener("click", () => {
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
});

refreshConfig();
loadFailureTests();
