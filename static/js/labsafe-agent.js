(function () {
  function normalizeAttachment(att) {
    const file = att.file || att;
    return {
      id: file.file_id || file.id || "",
      name: file.name || file.filename || "attachment",
      type: file.type || "",
      url: file.preview_url || file.url || file.content_url || "",
      thumbnail: file.thumbnail_url || file.thumbnail || file.preview_url || "",
      size: file.size || 0
    };
  }

  function renderAttachments(container, attachments) {
    const list = (attachments || []).map(normalizeAttachment);
    if (!list.length) return;
    const wrap = document.createElement("div");
    wrap.className = "attachment-grid";
    wrap.innerHTML = list.map((att) => {
      const thumb = att.thumbnail && (att.type === "image" || /\.(png|jpg|jpeg|webp)$/i.test(att.name))
        ? `<img src="${LabSafe.escapeHtml(att.thumbnail)}" alt="">`
        : `<div class="status-chip">文件</div>`;
      return `<a class="attachment-card" href="${LabSafe.escapeHtml(att.url || att.thumbnail || "#")}" target="_blank">
        ${thumb}
        <span>
          <strong>${LabSafe.escapeHtml(att.name)}</strong><br>
          <span class="muted mono">${LabSafe.escapeHtml(att.id || att.type || "uploaded")}</span>
        </span>
      </a>`;
    }).join("");
    container.appendChild(wrap);
  }

  function createAgentConsole(config) {
    const root = typeof config.root === "string" ? document.querySelector(config.root) : config.root;
    if (!root) return null;
    const thread = root.querySelector(config.thread || "[data-agent-thread]");
    const input = root.querySelector(config.input || "[data-agent-input]");
    const sendBtn = root.querySelector(config.send || "[data-agent-send]");
    const attachBtn = root.querySelector(config.attach || "[data-agent-attach]");
    const fileInput = root.querySelector(config.file || "[data-agent-file]");
    const uploadStrip = root.querySelector(config.uploadStrip || "[data-agent-uploads]");
    const deepBtn = root.querySelector(config.deep || "[data-agent-deep]");
    const searchBtn = root.querySelector(config.search || "[data-agent-search]");
    const modelBtn = root.querySelector(config.modelToggle || "[data-agent-model-toggle]");
    const modelPanel = root.querySelector(config.modelPanel || "[data-agent-model-panel]");
    const providerSelect = root.querySelector(config.provider || "[data-agent-provider]");
    const modelInput = root.querySelector(config.modelInput || "[data-agent-model-input]");
    const applyModelBtn = root.querySelector(config.applyModel || "[data-agent-apply-model]");
    const testModelBtn = root.querySelector(config.testModel || "[data-agent-test-model]");
    const modelHint = root.querySelector(config.modelHint || "[data-agent-model-hint]");
    const actions = root.querySelector(config.actions || "[data-agent-actions]");
    const statusNode = root.querySelector(config.status || "[data-agent-status]");
    const sessionKey = config.sessionKey || "labsafe.agent.console.session_id";
    const sender = config.sender || "web";
    let pendingUploads = [];
    let busy = false;
    let sessionId = localStorage.getItem(sessionKey);
    if (!sessionId) {
      sessionId = `${sender}:${Date.now().toString(36)}-${Math.random().toString(16).slice(2)}`;
      localStorage.setItem(sessionKey, sessionId);
    }

    function scrollBottom() {
      if (thread) thread.scrollTop = thread.scrollHeight;
    }

    function addMessage(role, text, meta = {}) {
      const node = document.createElement("article");
      node.className = `agent-message ${role === "user" ? "user" : "agent"}`;
      const risk = meta.risk_level ? LabSafe.makeBadge(meta.risk_level) : "";
      const trace = meta.trace_id ? `<span class="mono">${LabSafe.escapeHtml(meta.trace_id)}</span>` : "";
      node.innerHTML = `
        <div class="message-meta">
          <span>${role === "user" ? "我" : "LabSafe Agent"}</span>
          <span>${risk}${trace}</span>
        </div>
        <div class="message-body">${LabSafe.markdownLite(text || "")}</div>
      `;
      renderAttachments(node, meta.attachments);
      if (meta.model || meta.search || meta.tool_context || meta.conversation_memory) {
        const details = document.createElement("details");
        details.className = "message-details";
        details.innerHTML = `<summary>运行详情</summary>
          <div class="mono muted" style="margin-top:8px;white-space:pre-wrap;">${LabSafe.escapeHtml(JSON.stringify({
            model: meta.model,
            fallback: meta.model?.fallback_used,
            search: meta.search,
            memory: meta.conversation_memory,
            trace_id: meta.trace_id
          }, null, 2))}</div>`;
        node.appendChild(details);
      }
      thread?.appendChild(node);
      scrollBottom();
      return node;
    }

    function renderPendingUploads() {
      if (!uploadStrip) return;
      uploadStrip.innerHTML = pendingUploads.map((file, index) => `
        <span class="status-chip">
          ${LabSafe.escapeHtml(file.name || file.file_id)}
          <button class="btn btn-ghost" data-remove-upload="${index}" style="min-height:22px;padding:1px 5px;">x</button>
        </span>
      `).join("");
      uploadStrip.querySelectorAll("[data-remove-upload]").forEach((btn) => {
        btn.addEventListener("click", () => {
          pendingUploads.splice(Number(btn.dataset.removeUpload), 1);
          renderPendingUploads();
        });
      });
    }

    async function uploadFiles(files) {
      const selected = Array.from(files || []);
      if (!selected.length) return;
      attachBtn && (attachBtn.disabled = true);
      for (const file of selected) {
        const form = new FormData();
        form.append("file", file);
        try {
          const data = await LabSafe.api("/api/agent/uploads", { method: "POST", body: form });
          if (data.success === false) throw new Error(data.error || "上传失败");
          pendingUploads.push(data.file || data);
          LabSafe.toast(`已上传 ${file.name}`);
        } catch (error) {
          LabSafe.toast(`${file.name}: ${error.message}`, "error");
        }
      }
      attachBtn && (attachBtn.disabled = false);
      renderPendingUploads();
    }

    async function sendMessage() {
      if (busy) return;
      const content = input?.value.trim() || "";
      if (!content && !pendingUploads.length) return;
      const uploadSnapshot = pendingUploads.slice();
      pendingUploads = [];
      renderPendingUploads();
      input && (input.value = "");
      addMessage("user", content || "发送附件", { attachments: uploadSnapshot });
      busy = true;
      sendBtn && (sendBtn.disabled = true);
      const loading = addMessage("agent", "正在读取状态、分析上下文并生成回答...");
      try {
        const data = await LabSafe.post("/api/agent/chat", {
          message: content,
          sender,
          session_id: sessionId,
          deep_thinking: deepBtn?.classList.contains("active") || deepBtn?.ariaPressed === "true",
          web_search: searchBtn?.classList.contains("active") || searchBtn?.ariaPressed === "true",
          attachment_ids: uploadSnapshot.map((item) => item.file_id || item.id).filter(Boolean)
        }, { timeoutMs: 70000 });
        loading.remove();
        addMessage("agent", data.reply || "Agent 暂无回复", data);
        renderActions(data.pending_actions || data.proposed_actions || []);
        LabSafe.refreshShellStatus();
      } catch (error) {
        loading.remove();
        addMessage("agent", `请求失败：${error.message}`, { risk_level: "warning" });
      } finally {
        busy = false;
        sendBtn && (sendBtn.disabled = false);
      }
    }

    function renderActions(list) {
      if (!actions) return;
      const actionList = list || [];
      actions.innerHTML = "";
      if (!actionList.length) return;
      actionList.forEach((action) => {
        const token = action.token || action.confirmation_token || "";
        const card = document.createElement("div");
        card.className = "confirm-card";
        card.innerHTML = `
          <strong>${LabSafe.escapeHtml(action.name || action.action || "高风险动作")}</strong>
          <p class="secondary">${LabSafe.escapeHtml(action.reason || action.description || "该动作需要人工确认。")}</p>
          <div class="control-row">
            <button class="btn btn-danger">确认执行</button>
            <button class="btn btn-ghost">取消</button>
          </div>`;
        card.querySelector(".btn-danger").addEventListener("click", () => {
          LabSafe.openModal({
            title: "二次确认高风险动作",
            danger: true,
            body: `<p>动作：<strong>${LabSafe.escapeHtml(action.name || action.action || "高风险动作")}</strong></p><p>${LabSafe.escapeHtml(action.reason || "确认后将请求后端执行。")}</p>`,
            confirmText: "确认执行",
            onConfirm: async () => {
              const data = await LabSafe.post("/api/agent/action/confirm", { token });
              if (data.success === false) throw new Error(data.error || "确认失败");
              LabSafe.toast("确认动作已提交");
              card.remove();
            }
          });
        });
        card.querySelector(".btn-ghost").addEventListener("click", () => card.remove());
        actions.appendChild(card);
      });
    }

    async function refreshStatus() {
      try {
        const [status, models] = await Promise.all([LabSafe.api("/api/agent/status"), LabSafe.api("/api/agent/models")]);
        if (statusNode) {
          statusNode.innerHTML = `${LabSafe.makeBadge(status.risk_level || "normal")} <span class="secondary">${LabSafe.escapeHtml(status.reason || "助手就绪")}</span>`;
        }
        renderActions(status.pending_actions || status.proposed_actions || []);
        renderModels(models);
      } catch (error) {
        if (statusNode) statusNode.innerHTML = `<span class="status-chip status-warning">助手状态不可用</span>`;
      }
    }

    function renderModels(data) {
      if (!providerSelect) return;
      const providers = data.providers || data.models?.providers || [];
      const activeProvider = data.active_provider || data.models?.active_provider || "";
      const activeModel = data.active_model || data.models?.active_model || "";
      providerSelect.innerHTML = providers.map((provider) => `
        <option value="${LabSafe.escapeHtml(provider.id)}" data-model="${LabSafe.escapeHtml(provider.model || provider.default_model || "")}" ${provider.id === activeProvider ? "selected" : ""}>
          ${LabSafe.escapeHtml(provider.label || provider.id)}
        </option>
      `).join("");
      if (modelInput && activeModel) modelInput.value = activeModel;
      if (modelHint) {
        modelHint.textContent = activeModel ? `当前模型 ${activeProvider}/${activeModel}` : "API key 不在页面明文展示。";
      }
    }

    async function applyModel() {
      const provider = providerSelect?.value || "";
      const model = modelInput?.value.trim() || "";
      if (!provider) return;
      applyModelBtn && (applyModelBtn.disabled = true);
      try {
        const data = await LabSafe.post("/api/agent/models/select", {
          provider,
          model
        });
        if (data.success === false) throw new Error(data.error || "切换失败");
        const selected = providerSelect?.selectedOptions?.[0]?.textContent?.trim() || provider;
        LabSafe.toast(`已切换到 ${selected}`);
        refreshStatus();
      } catch (error) {
        LabSafe.toast(error.message, "error");
      } finally {
        applyModelBtn && (applyModelBtn.disabled = false);
      }
    }

    async function testModel() {
      testModelBtn && (testModelBtn.disabled = true);
      try {
        const data = await LabSafe.post("/api/agent/models/test", {
          provider: providerSelect?.value || "",
          model: modelInput?.value.trim() || ""
        }, { timeoutMs: 25000 });
        if (data.success === false) throw new Error(data.error || "测试失败");
        LabSafe.toast(data.model?.success === false ? "模型测试失败，后端会 fallback" : "模型测试完成");
      } catch (error) {
        LabSafe.toast(error.message, "error");
      } finally {
        testModelBtn && (testModelBtn.disabled = false);
      }
    }

    function toggleButton(btn) {
      if (!btn) return;
      const active = !btn.classList.contains("active");
      if (!btn.dataset.inactiveText) {
        btn.dataset.inactiveText = btn.textContent.trim();
      }
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
      btn.textContent = active
        ? (btn.dataset.activeText || `${btn.dataset.inactiveText}已开`)
        : btn.dataset.inactiveText;
      btn.title = active ? "已开启，发送下一条消息时生效" : "已关闭";
    }

    sendBtn?.addEventListener("click", sendMessage);
    input?.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) sendMessage();
    });
    attachBtn?.addEventListener("click", () => fileInput?.click());
    fileInput?.addEventListener("change", () => uploadFiles(fileInput.files));
    deepBtn?.addEventListener("click", () => toggleButton(deepBtn));
    searchBtn?.addEventListener("click", () => toggleButton(searchBtn));
    modelBtn?.addEventListener("click", () => modelPanel?.classList.toggle("open"));
    applyModelBtn?.addEventListener("click", applyModel);
    testModelBtn?.addEventListener("click", testModel);
    providerSelect?.addEventListener("change", () => {
      const opt = providerSelect.selectedOptions[0];
      if (modelInput && opt?.dataset.model) modelInput.value = opt.dataset.model;
      applyModel();
    });

    refreshStatus();
    window.setInterval(refreshStatus, config.pollMs || 7000);
    return { refreshStatus, sendMessage, uploadFiles, addMessage };
  }

  window.LabSafeAgentConsole = { create: createAgentConsole };
})();
