const modelEndpoint = "/api/v1/observability/models/";
const byId = (id) => document.getElementById(id);

async function fetchModelActivity() {
  const response = await fetch(modelEndpoint, {
    headers: { Accept: "application/json" },
    method: "GET",
  });
  if (!response.ok) {
    throw new Error("model_activity_unavailable");
  }
  return response.json();
}

function labelRole(role) {
  return role.charAt(0).toUpperCase() + role.slice(1);
}

function statePill(state) {
  const pill = document.createElement("span");
  pill.className = `state-pill state-${state}`;
  pill.textContent = state;
  return pill;
}

function renderModels(payload) {
  const source = payload.source === "synthetic_fixture" ? "Synthetic fixture" : "Live process";
  byId("model-source").textContent = source;

  const body = byId("model-activity-body");
  body.replaceChildren();
  payload.models.forEach((model) => {
    const row = document.createElement("tr");
    const role = document.createElement("th");
    role.scope = "row";
    role.textContent = labelRole(model.role);
    const configured = document.createElement("td");
    configured.textContent = model.configured_model || "Not configured";
    const observed = document.createElement("td");
    observed.textContent = model.observed_models.length
      ? model.observed_models.join(", ")
      : "No name reported";
    const states = document.createElement("td");
    states.className = "state-cell";
    if (model.states.length) {
      model.states.forEach((state) => states.append(statePill(state)));
    } else {
      states.textContent = "No evidence";
      states.classList.add("text-secondary");
    }
    const count = document.createElement("td");
    count.textContent = String(model.event_count);
    row.append(role, configured, observed, states, count);
    body.append(row);
  });

  const cards = byId("provider-cards");
  cards.replaceChildren();
  payload.providers.forEach((provider) => {
    const column = document.createElement("div");
    column.className = "col-md-6";
    const card = document.createElement("article");
    card.className = "provider-card rounded-3 p-3 h-100";
    const title = document.createElement("h3");
    title.className = "h6 text-capitalize";
    title.textContent = `${provider.provider} provider`;
    const detail = document.createElement("p");
    detail.className = "small text-secondary mb-0";
    detail.textContent = provider.observed
      ? `${provider.attempts} attempts · ${provider.retries} retries${provider.degraded ? " · degraded" : ""}`
      : "Not observed in retained runs";
    card.append(title, detail);
    column.append(card);
    cards.append(column);
  });
  byId("model-refresh-status").textContent = `Updated ${new Date().toLocaleTimeString()}`;
}

async function refreshModels() {
  try {
    renderModels(await fetchModelActivity());
  } catch {
    byId("model-refresh-status").textContent = "Updates unavailable; showing last known state.";
  }
}

refreshModels();
window.setInterval(refreshModels, 3000);
