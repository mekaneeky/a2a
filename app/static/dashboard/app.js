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

function agentOptions(filterFn) {
  const agents = state.payload?.agents || [];
  return agents.filter((agent) => (filterFn ? filterFn(agent) : true));
}

function setSelectOptions(selectId, options, selectedValue = null) {
  const select = $(selectId);
  const previous = selectedValue ?? select.value;
  select.innerHTML = "";
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
    ["name", "role", "reputation", "balance", "id"],
    agents.map((agent) => [agent.name, agent.role || "-", agent.reputation, agent.balance, agent.id]),
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

  const buyers = agentOptions((agent) => agent.role === "buyer").map((agent) => ({
    value: agent.id,
    label: `${agent.name} (${agent.balance})`,
  }));
  const sellers = agentOptions((agent) => agent.role === "seller").map((agent) => ({
    value: agent.id,
    label: `${agent.name} (${agent.balance})`,
  }));
  const allAgents = agents.map((agent) => ({
    value: agent.id,
    label: `${agent.name} [${agent.role || "external"}]`,
  }));

  setSelectOptions("faucet-agent", buyers);
  setSelectOptions("listing-agent", allAgents);
  setSelectOptions("search-buyer", buyers);
  setSelectOptions("handshake-buyer", buyers);
  setSelectOptions("deliver-seller", sellers);
  setSelectOptions("artifact-buyer", buyers);
  setSelectOptions("decision-buyer", buyers);
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
      log(`Created ${agent.role} agent ${agent.name} (${agent.id})`);
      await refreshState();
    } catch (err) {
      log(err.message, true);
    }
  });

  $("faucet-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const agentId = $("faucet-agent").value;
      const amount = Number($("faucet-amount").value);
      const result = await request(`/ui/api/agents/${agentId}/faucet`, {
        method: "POST",
        body: JSON.stringify({ amount }),
      });
      log(`Faucet tx=${result.tx_id} new_balance=${result.balance}`);
      await refreshState();
    } catch (err) {
      log(err.message, true);
    }
  });

  $("listing-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const payload = {
        agent_id: $("listing-agent").value,
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
    }
  });

  $("search-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const buyerId = $("search-buyer").value;
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
        $("handshake-offer").value = first.offer.listing_id;
        $("handshake-demand").value = guessLatestDemandIdForBuyer(buyerId);
        log(`Search found ${state.searchResults.length} result(s).`);
      }
    } catch (err) {
      log(err.message, true);
    }
  });

  $("handshake-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const priceText = $("handshake-price").value.trim();
      const payload = {
        buyer_id: $("handshake-buyer").value,
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
    }
  });

  $("deliver-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const contractId = $("deliver-contract").value.trim();
      const payload = {
        seller_id: $("deliver-seller").value,
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
    }
  });

  $("artifact-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const contractId = $("artifact-contract").value.trim();
      const payload = {
        buyer_id: $("artifact-buyer").value,
      };
      const artifact = await request(`/ui/api/contracts/${contractId}/artifact`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      $("artifact-output").textContent = artifact.plaintext;
      log(`Decrypted artifact for contract ${contractId}.`);
    } catch (err) {
      log(err.message, true);
    }
  });

  $("decision-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const contractId = $("decision-contract").value.trim();
      const payload = {
        buyer_id: $("decision-buyer").value,
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
