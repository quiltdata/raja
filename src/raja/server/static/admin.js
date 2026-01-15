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

refreshConfig();
