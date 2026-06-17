// Tiny vanilla SSE consumer for the landing page run-form.
// No build step, no framework.

(function () {
  const form = document.getElementById("run-form");
  if (!form) return;

  const status = document.getElementById("run-status");
  const runIdEl = document.getElementById("run-id");
  const log = document.getElementById("event-log");
  const link = document.getElementById("results-link");

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const gitUrl = document.getElementById("git_url").value.trim();
    if (!gitUrl) return;

    status.hidden = false;
    log.innerHTML = "";
    runIdEl.textContent = "starting…";

    let runId;
    try {
      const res = await fetch("/api/run", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ git_url: gitUrl }),
      });
      if (!res.ok) throw new Error(`POST /api/run -> ${res.status}`);
      const j = await res.json();
      runId = j.id;
    } catch (err) {
      runIdEl.textContent = "error: " + err.message;
      return;
    }

    runIdEl.textContent = runId;
    link.href = `/run/${runId}`;
    link.textContent = `view results for ${runId} →`;

    const es = new EventSource(`/api/run/${runId}/stream`);
    es.onmessage = (msg) => appendEvent(JSON.parse(msg.data));
    // SSE library uses named events keyed by `kind`; listen for the common ones.
    ["start", "begin", "end", "node_done", "done", "error", "tick"].forEach((k) =>
      es.addEventListener(k, (msg) => appendEvent(JSON.parse(msg.data)))
    );

    function appendEvent(ev) {
      const li = document.createElement("li");
      const agent = document.createElement("span");
      agent.className = "agent";
      agent.textContent = ev.agent || "?";
      const kind = document.createElement("span");
      kind.className = "kind";
      kind.textContent = ev.kind || "";
      const at = document.createElement("span");
      at.className = "at";
      at.textContent = `+${(ev.at || 0).toFixed(1)}s`;
      li.appendChild(agent);
      li.appendChild(kind);
      if (ev.message) {
        const msg = document.createElement("span");
        msg.style.marginLeft = "8px";
        msg.textContent = ev.message;
        li.appendChild(msg);
      }
      li.appendChild(at);
      log.appendChild(li);
      log.scrollTop = log.scrollHeight;
      if (ev.kind === "done" || ev.kind === "error") {
        es.close();
      }
    }
  });
})();
