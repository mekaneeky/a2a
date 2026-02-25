const state = {
  payload: null,
  searchResults: [],
};

const $ = (id) => document.getElementById(id);

function parseCsv(text) {
  return text
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

function timestamp() {
  return new Date().toLocaleTimeString();
}

function log(message, isError = false) {
  const events = $("events");
  const prefix = isError ? "[error]" : "[ok]";
  events.textContent = `[${timestamp()}] ${prefix} ${message}\n${events.textContent}`;
}

function showAgentHintOnError(errorText) {
  if (!errorText.includes("Unknown dashboard agent")) {
    return;
  }
  log(
    "Selected agent is not dashboard-managed. Use agents created in this dashboard for signing actions.",
    true,
  );
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "content-type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch (_err) {
      // keep fallback detail
    }
    throw new Error(detail);
  }
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

function hasRole(agent, role) {
  if (Array.isArray(agent.roles)) {
    return agent.roles.includes(role);
  }
  if (!agent.role) {
    return false;
  }
  return String(agent.role)
    .split(",")
    .map((item) => item.trim())
    .includes(role);
}

function roleLabel(agent) {
  if (Array.isArray(agent.roles) && agent.roles.length > 0) {
    return agent.roles.join(",");
  }
  return agent.role || "-";
}

function agentOptions(filterFn) {
  const agents = state.payload?.agents || [];
  return agents.filter((agent) => (filterFn ? filterFn(agent) : true));
}

function setSelectOptions(selectId, options, selectedValue = null, emptyLabel = "No options") {
  const select = $(selectId);
  const previous = selectedValue ?? select.value;
  select.innerHTML = "";
  if (!options.length) {
    const emptyOption = document.createElement("option");
    emptyOption.value = "";
    emptyOption.textContent = emptyLabel;
    select.appendChild(emptyOption);
    return;
  }
  for (const item of options) {
    const option = document.createElement("option");
    option.value = item.value;
    option.textContent = item.label;
    if (item.value === previous) {
      option.selected = true;
    }
    select.appendChild(option);
  }
}

function selectedValueOrThrow(selectId, label) {
  const value = $(selectId).value;
  if (!value) {
    throw new Error(`${label} not available`);
  }
  return value;
}

function table(headers, rows) {
  if (!rows.length) {
    return "<p style='padding:8px;margin:0'>No data yet.</p>";
  }
  const head = headers.map((header) => `<th>${header}</th>`).join("");
  const body = rows
    .map(
      (row) =>
        `<tr>${row
          .map((cell) => `<td>${String(cell ?? "").replaceAll("<", "&lt;").replaceAll(">", "&gt;")}</td>`)
          .join("")}</tr>`,
    )
    .join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderSearchResults() {
  const root = $("search-results");
  if (!state.searchResults.length) {
    root.innerHTML = "<p>No search results yet.</p>";
    return;
  }

  root.innerHTML = state.searchResults
    .map((item) => {
      const reasons = item.reasons?.length ? item.reasons.join(", ") : "none";
      const badgeClass = item.card_match ? "pill" : "pill fail";
      const badgeLabel = item.card_match ? "Card Match" : "Non Match";
      return `
        <article class="card">
          <div class="card-head">
            <strong>${item.seller.name}</strong>
            <span class="${badgeClass}">${badgeLabel}</span>
          </div>
          <div>offer=${item.offer.listing_id}</div>
          <div>sku=${item.offer.sku}, price=${item.offer.price_credits}</div>
          <div>reasons=${reasons}</div>
          <button
            class="btn btn-outline use-offer"
            data-offer-id="${item.offer.listing_id}"
            data-seller-id="${item.seller.id}"
            type="button"
          >Use This Offer</button>
        </article>
      `;
    })
    .join("");

  for (const button of document.querySelectorAll(".use-offer")) {
    button.addEventListener("click", () => {
      $("handshake-offer").value = button.dataset.offerId || "";
      log(`Selected offer ${button.dataset.offerId} from seller ${button.dataset.sellerId}`);
    });
  }
}

function renderLocalRunner() {
  const examples = state.payload?.local_examples || [];
  const runs = state.payload?.local_runs || [];

  const buyersRoot = $("local-buyer-examples");
  const sellersRoot = $("local-seller-examples");
  const runsRoot = $("local-runs");

  const buyerExamples = examples.filter((item) => item.role === "buyer");
  const sellerExamples = examples.filter((item) => item.role === "seller");

  function buildExampleCards(items) {
    if (!items.length) {
      return "<p>No local examples found.</p>";
    }
    return items
      .map(
        (item) => `
          <article class="card">
            <div class="card-head">
              <strong>${item.id}</strong>
              <span class="pill">${item.role}</span>
            </div>
            <div>${item.command}</div>
            <div>${item.path}</div>
            <button class="btn btn-solid run-example" data-example-id="${item.id}" type="button">
              Run ${item.id}
            </button>
          </article>
        `,
      )
      .join("");
  }

  function buildRunCards(items) {
    if (!items.length) {
      return "<p>No runs yet.</p>";
    }
    return items
      .map(
        (item) => `
          <article class="card">
            <div class="card-head">
              <strong>${item.example_id}</strong>
              <span class="pill ${item.status === "failed" ? "fail" : ""}">${item.status}</span>
            </div>
            <div>pid=${item.pid ?? "-"}</div>
            <div>run=${item.run_id}</div>
            <button class="btn btn-outline view-log" data-run-id="${item.run_id}" type="button">View Log</button>
            <button class="btn btn-outline stop-run" data-run-id="${item.run_id}" type="button">Stop</button>
          </article>
        `,
      )
      .join("");
  }

  buyersRoot.innerHTML = buildExampleCards(buyerExamples);
  sellersRoot.innerHTML = buildExampleCards(sellerExamples);
  runsRoot.innerHTML = buildRunCards(runs);

  for (const button of document.querySelectorAll(".run-example")) {
    button.addEventListener("click", async () => {
      const exampleId = button.dataset.exampleId;
      if (!exampleId) {
        return;
      }
      try {
        const run = await request(`/ui/api/local/examples/${exampleId}/start`, {
          method: "POST",
          body: "{}",
        });
        log(`Started local example ${exampleId} run=${run.run_id}`);
        await refreshState();
      } catch (err) {
        log(err.message, true);
      }
    });
  }

  for (const button of document.querySelectorAll(".stop-run")) {
    button.addEventListener("click", async () => {
      const runId = button.dataset.runId;
      if (!runId) {
        return;
      }
      try {
        const run = await request(`/ui/api/local/runs/${runId}/stop`, {
          method: "POST",
          body: "{}",
        });
        log(`Stopped run ${runId} status=${run.status}`);
        await refreshState();
      } catch (err) {
        log(err.message, true);
      }
    });
  }

  for (const button of document.querySelectorAll(".view-log")) {
    button.addEventListener("click", async () => {
      const runId = button.dataset.runId;
      if (!runId) {
        return;
      }
      try {
        const payload = await request(`/ui/api/local/runs/${runId}/log?tail=200`, { method: "GET", headers: {} });
        $("local-log-output").textContent = payload.log || "";
        log(`Loaded log for run ${runId}`);
      } catch (err) {
        log(err.message, true);
      }
    });
  }
}

function renderState() {
  const payload = state.payload;
  if (!payload) {
    return;
  }

  $("backend-badge").textContent = `ledger: ${payload.ledger_backend}`;

  const agents = payload.agents || [];
  const listings = payload.listings || [];
  const contracts = payload.contracts || [];
  const ledgerEntries = payload.ledger_entries || [];

  $("agents-table").innerHTML = table(
    ["name", "roles", "managed", "reputation", "balance", "id"],
    agents.map((agent) => [
      agent.name,
      roleLabel(agent),
      agent.ui_managed ? "yes" : "no",
      agent.reputation,
      agent.balance,
      agent.id,
    ]),
  );
  $("listings-table").innerHTML = table(
    ["kind", "sku", "price", "agent", "id"],
    listings.map((item) => [item.kind, item.sku, item.price_credits, item.agent_id, item.id]),
  );
  $("contracts-table").innerHTML = table(
    ["status", "sku", "price", "buyer", "seller", "id"],
    contracts.map((item) => [item.status, item.sku, item.price_credits, item.buyer_id, item.seller_id, item.id]),
  );
  $("ledger-table").innerHTML = table(
    ["tx_id", "account", "amount", "reason", "contract"],
    ledgerEntries.map((item) => [item.tx_id, item.account, item.amount, item.reason, item.contract_id || "-"]),
  );

  const managedBuyers = agentOptions((agent) => agent.ui_managed && hasRole(agent, "buyer")).map((agent) => ({
    value: agent.id,
    label: `${agent.name} (${agent.balance})`,
  }));
  const managedSellers = agentOptions((agent) => agent.ui_managed && hasRole(agent, "seller")).map((agent) => ({
    value: agent.id,
    label: `${agent.name} (${agent.balance})`,
  }));
  const managedAgents = agentOptions((agent) => agent.ui_managed).map((agent) => ({
    value: agent.id,
    label: `${agent.name} [${roleLabel(agent)}]`,
  }));

  setSelectOptions("faucet-agent", managedBuyers, null, "No managed buyer");
  setSelectOptions("listing-agent", managedAgents, null, "No managed agent");
  setSelectOptions("search-buyer", managedBuyers, null, "No managed buyer");
  setSelectOptions("handshake-buyer", managedBuyers, null, "No managed buyer");
  setSelectOptions("deliver-seller", managedSellers, null, "No managed seller");
  setSelectOptions("artifact-buyer", managedBuyers, null, "No managed buyer");
  setSelectOptions("decision-buyer", managedBuyers, null, "No managed buyer");

  renderLocalRunner();
}

async function refreshState() {
  state.payload = await request("/ui/api/state", { method: "GET", headers: {} });
  renderState();
  renderSearchResults();
}

function guessLatestDemandIdForBuyer(buyerId) {
  const listings = state.payload?.listings || [];
  const demand = listings.find((item) => item.kind === "demand" && item.agent_id === buyerId);
  return demand ? demand.id : "";
}

function installHandlers() {
  $("refresh-state").addEventListener("click", async () => {
    try {
      await refreshState();
      log("State refreshed");
    } catch (err) {
      log(err.message, true);
    }
  });

  $("refresh-local").addEventListener("click", async () => {
    try {
      await refreshState();
      log("Local runner refreshed");
    } catch (err) {
      log(err.message, true);
    }
  });

  $("agent-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const payload = {
        name: $("agent-name").value.trim(),
        role: $("agent-role").value,
        skus: parseCsv($("agent-skus").value),
        capabilities: parseCsv($("agent-caps").value),
        tags: parseCsv($("agent-tags").value),
        description: $("agent-desc").value.trim() || null,
      };
      const agent = await request("/ui/api/agents", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      log(`Created ${roleLabel(agent)} agent ${agent.name} (${agent.id})`);
      await refreshState();
    } catch (err) {
      log(err.message, true);
      showAgentHintOnError(String(err.message));
    }
  });

  $("faucet-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const agentId = selectedValueOrThrow("faucet-agent", "Buyer");
      const amount = Number($("faucet-amount").value);
      const result = await request(`/ui/api/agents/${agentId}/faucet`, {
        method: "POST",
        body: JSON.stringify({ amount }),
      });
      log(`Faucet tx=${result.tx_id} new_balance=${result.balance}`);
      await refreshState();
    } catch (err) {
      log(err.message, true);
      showAgentHintOnError(String(err.message));
    }
  });

  $("listing-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const payload = {
        agent_id: selectedValueOrThrow("listing-agent", "Agent"),
        kind: $("listing-kind").value,
        sku: $("listing-sku").value.trim(),
        price_credits: Number($("listing-price").value),
        description: $("listing-description").value.trim(),
        rfq_enabled: false,
      };
      const listing = await request("/ui/api/listings", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      log(`Created ${listing.kind} listing ${listing.id}`);
      await refreshState();
      if (listing.kind === "demand") {
        $("handshake-demand").value = listing.id;
      }
    } catch (err) {
      log(err.message, true);
      showAgentHintOnError(String(err.message));
    }
  });

  $("search-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const buyerId = selectedValueOrThrow("search-buyer", "Buyer");
      const payload = {
        buyer_id: buyerId,
        sku: $("search-sku").value.trim(),
        required_capabilities: parseCsv($("search-caps").value),
        required_tags: parseCsv($("search-tags").value),
        max_price_credits: Number($("search-max-price").value),
        require_online: $("search-online").value === "true",
        include_non_matching: $("search-non-matching").value === "true",
      };
      const search = await request("/ui/api/search", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      state.searchResults = search.results || [];
      renderSearchResults();
      if (!state.searchResults.length) {
        log("Search completed with no sellers.");
      } else {
        const first = state.searchResults[0];
        $("handshake-buyer").value = buyerId;
        $("handshake-offer").value = first.offer.listing_id;
        $("handshake-demand").value = guessLatestDemandIdForBuyer(buyerId);
        log(`Search found ${state.searchResults.length} result(s).`);
      }
    } catch (err) {
      log(err.message, true);
      showAgentHintOnError(String(err.message));
    }
  });

  $("handshake-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const priceText = $("handshake-price").value.trim();
      const payload = {
        buyer_id: selectedValueOrThrow("handshake-buyer", "Buyer"),
        demand_listing_id: $("handshake-demand").value.trim(),
        offer_listing_id: $("handshake-offer").value.trim(),
        terms: $("handshake-terms").value.trim(),
      };
      if (priceText) {
        payload.price_credits = Number(priceText);
      }
      const contract = await request("/ui/api/contracts/handshake-activate", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      $("deliver-contract").value = contract.id;
      $("artifact-contract").value = contract.id;
      $("decision-contract").value = contract.id;
      log(`Contract ${contract.id} activated.`);
      await refreshState();
    } catch (err) {
      log(err.message, true);
      showAgentHintOnError(String(err.message));
    }
  });

  $("deliver-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const contractId = $("deliver-contract").value.trim();
      const payload = {
        seller_id: selectedValueOrThrow("deliver-seller", "Seller"),
        payload_text: $("deliver-payload").value,
      };
      const delivery = await request(`/ui/api/contracts/${contractId}/deliver`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      log(`Delivery for contract ${contractId}: ${delivery.status}`);
      await refreshState();
    } catch (err) {
      log(err.message, true);
      showAgentHintOnError(String(err.message));
    }
  });

  $("artifact-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const contractId = $("artifact-contract").value.trim();
      const payload = {
        buyer_id: selectedValueOrThrow("artifact-buyer", "Buyer"),
      };
      const artifact = await request(`/ui/api/contracts/${contractId}/artifact`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      $("artifact-output").textContent = artifact.plaintext;
      log(`Decrypted artifact for contract ${contractId}.`);
    } catch (err) {
      log(err.message, true);
      showAgentHintOnError(String(err.message));
    }
  });

  $("decision-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const contractId = $("decision-contract").value.trim();
      const payload = {
        buyer_id: selectedValueOrThrow("decision-buyer", "Buyer"),
        accept: $("decision-accept").value === "true",
      };
      const result = await request(`/ui/api/contracts/${contractId}/decision`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      log(`Settlement for contract ${contractId}: ${result.outcome}`);
      await refreshState();
    } catch (err) {
      log(err.message, true);
      showAgentHintOnError(String(err.message));
    }
  });
}

async function init() {
  installHandlers();
  await refreshState();
  log("Dashboard ready.");
  window.setInterval(async () => {
    try {
      await refreshState();
    } catch (_err) {
      // Skip noisy polling errors.
    }
  }, 4000);
}

init().catch((err) => log(err.message, true));
