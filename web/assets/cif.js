/* ============================================================
   cifkit 工作台 · assets/cif.js
   ------------------------------------------------------------
   cifkit 专属适配层，加载顺序必须在 assets/app.js 之后。
   它只做一件事：**只替换 EduKit.EduAPI.run 的接后端路径**，
   外加渲染本项目的「六场景窄闭环」卡片；上游 app.js 一个字节都不改
   （见 web/ADAPTATION.md，app.js 与上游哈希一致）。

   契约真源：CONTRACT.md（冻结）
     POST /api/route        一句话 → {spec, plan, warnings}
     POST /api/job          {kind, text, spec, plan} → {job}
     GET  /api/job/events   SSE：meta/log/step/notice/error/done
     POST /api/job/cancel   {id} → {job}
     POST /api/op           {op, params, run, input} → {ok, op, run, result, products, argv, elapsed_ms, warnings}
     GET  /api/bootstrap    首屏元数据（健康态唯一判据）
     GET  /api/runs         runs 列表
     GET  /api/run?id=      run 快照（manifest / report_md / artifacts / validation）
     GET  /api/artifact     产物字节（下载 / PNG 预览）
     GET  /api/sources      数据源状态（离线，不 probe）

   两种运行态：
     · 后端在线：结构化调用 + 结构化结果卡片（数字 / 校验 / 产物 / 预览 / 报告 / 下一步）
     · 后端不可达：不白屏，渲染 data.js 里预置的 mockResult，并在卡片顶部写死
       「离线演示（无后端）· 预置 mock，不是 cifkit 的真实输出」；下载按钮禁用。
   ============================================================ */

(() => {
  "use strict";

  const BQ3 = String.fromCharCode(96).repeat(3); // 三个反引号（构造 Markdown 围栏用）

  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));

  const ENDPOINTS = {
    bootstrap: "/api/bootstrap",
    runs: "/api/runs",
    run: "/api/run",
    artifact: "/api/artifact",
    sources: "/api/sources",
    formats: "/api/formats",
    route: "/api/route",
    op: "/api/op",
    job: "/api/job",
    jobEvents: "/api/job/events",
    jobCancel: "/api/job/cancel",
  };

  const state = {
    online: false,
    boot: null,
    runs: [],
    currentRun: null,
    snapshot: null,
    scenarioKey: null,
    formValues: {},
    inputMode: "cif_text",
    inputCif: "",
    inputPath: "",
    inputArtifact: "",
    busy: false,
    job: null,
    es: null,
    lastJob: null,
    logLines: [],
    jobSteps: {},
    sources: [],
    formats: {},
    bundles: {},
    pendingSpec: null,
    drifted: false,
    syncing: false,
  };

  /* ------------------------------------------------------------------ utils */

  function toast(message, kind) {
    const box = $("toast");
    const text = $("toastText");
    if (!box || !text) return;
    text.textContent = message;
    box.classList.toggle("is-error", kind === "error");
    box.classList.add("is-on");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => box.classList.remove("is-on"), kind === "error" ? 7000 : 3400);
  }

  function refreshIcons() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      try { window.lucide.createIcons(); } catch (error) { /* 图标不阻塞 */ }
    }
  }

  function humanBytes(bytes) {
    const n = Number(bytes);
    if (!Number.isFinite(n) || n < 0) return "—";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB（" + n + " B）";
    return (n / 1024 / 1024).toFixed(2) + " MB（" + n + " B）";
  }

  function scenarioByKey(key) {
    const list = (window.EDU_DATA && window.EDU_DATA.scenarios) || [];
    return list.find((item) => item.key === key) || null;
  }

  function currentScenario() {
    return scenarioByKey(state.scenarioKey) || scenarioByKey("kickoff");
  }

  /* ------------------------------------------------------------------ HTTP */

  function CifError(payload, status) {
    const detail = payload || {};
    const error = new Error(detail.message || detail.error || ("HTTP " + status));
    error.name = "CifError";
    error.code = detail.error || "";
    error.hint = detail.hint || "";
    error.status = status;
    error.payload = detail;
    return error;
  }

  async function getJson(url) {
    const response = await fetch(url, { method: "GET", headers: { Accept: "application/json" } });
    let data = null;
    try { data = await response.json(); } catch (error) { data = null; }
    if (!response.ok || !data || data.ok === false) throw CifError(data, response.status);
    return data;
  }

  async function postJson(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let data = null;
    try { data = await response.json(); } catch (error) { data = null; }
    if (!response.ok || !data || data.ok === false) throw CifError(data, response.status);
    return data;
  }

  // 只读模式（未开 --allow-exec / runs-dir 不可写）时，前端先自查：
  // 不发一次注定 403 的请求，但也要把后端的口径说清楚（CONTRACT §6 / §9.5）。
  function writeBlocked() {
    if (!state.online || !state.boot) return null;
    const caps = state.boot.caps || {};
    if (caps.exec === false) {
      return {
        code: "E_EXEC_DISABLED",
        message: "只读模式（未加 --allow-exec）：后端不接受 op / job",
        hint: "用 --allow-exec 重启 server.py；只读浏览（runs / run 快照 / artifact / sources）不受影响",
      };
    }
    if (caps.runs_writable === false) {
      return {
        code: "E_READONLY_RUNS",
        message: "当前 runs-dir 不可写，写动作被拒",
        hint: "换一个可写的 --runs-dir，或只做浏览",
      };
    }
    return null;
  }

  function errorLine(error) {
    // CONTRACT §6：error / message / hint 三项都要露出来。
    const code = error && error.code ? error.code : (error && error.status ? "HTTP " + error.status : "网络错误");
    const message = error && error.message ? error.message : "未知错误";
    const hint = error && error.hint ? error.hint : "";
    return { code: String(code), message: String(message), hint: String(hint) };
  }

  function showErrorCard(title, error, extraHtml) {
    const box = $("cifResult");
    if (!box) return;
    const info = errorLine(error);
    box.hidden = false;
    box.innerHTML =
      '<div class="cif-card cif-card-error">' +
        '<div class="cif-card-head"><span class="kicker">' + esc(title) + '</span>' +
        '<span class="cif-badge is-bad">失败</span></div>' +
        '<dl class="cif-err">' +
          '<div><dt>error</dt><dd><code>' + esc(info.code) + '</code></dd></div>' +
          '<div><dt>message</dt><dd>' + esc(info.message) + '</dd></div>' +
          '<div><dt>hint</dt><dd>' + (info.hint ? esc(info.hint) : '<span class="cif-muted">（后端未给 hint）</span>') + '</dd></div>' +
        '</dl>' +
        (extraHtml || "") +
      '</div>';
    toast("失败：" + info.code + " · " + info.message, "error");
    refreshIcons();
  }

  /* ------------------------------------------------------------------ 顶栏健康态 / runs */

  function setOnline(online) {
    const next = Boolean(online);
    const changed = next !== state.online;
    state.online = next;
    const runtime = $("runtimeText");
    if (runtime) {
      runtime.textContent = next
        ? "后端在线 · 只绑 127.0.0.1 · 数据不上传"
        : "离线演示（无后端）· 后端不可达，页面用预置 mock 渲染";
    }
    const badge = $("cifHealth");
    if (badge) {
      badge.classList.toggle("is-ok", next);
      badge.classList.toggle("is-off", !next);
      badge.innerHTML = '<i data-lucide="' + (next ? "plug-zap" : "unplug") + '"></i><span id="cifHealthText">' +
        (next ? "后端已连接" : "离线演示（无后端）") + '</span>';
    }
    const banner = $("cifOfflineBanner");
    if (banner) banner.hidden = next;
    if (window.EduKit && typeof window.EduKit.renderMode === "function") {
      try { window.EduKit.renderMode(); } catch (error) { /* 徽章不阻塞 */ }
    }
    if (changed) refreshIcons();
  }

  function renderTopbar() {
    const boot = state.boot;
    const kit = (boot && boot.cifkit) || {};
    const version = $("cifVersion");
    if (version) {
      version.textContent = state.online
        ? "cifkit " + (kit.version || "?") + " · api " + (kit.api_version || "?") +
          (kit.cache && kit.cache.ready ? " · cache ready" : " · cache 未就绪")
        : "后端不可达 · 走离线演示";
      version.title = state.online
        ? "root=" + (kit.root || "?") + " · runs=" + (kit.runs_dir || "?") + " · python=" + (kit.python || "?")
        : "GET /api/bootstrap 失败：页面降级为离线演示，不会白屏";
    }
    const caps = (boot && boot.caps) || {};
    const capBox = $("cifCaps");
    if (capBox) {
      const bits = [];
      bits.push(caps.exec ? "exec 可执行" : "exec 关闭（未加 --allow-exec）");
      bits.push(caps.runs_writable ? "runs 可写" : "runs 只读");
      bits.push(caps.network ? "本次动作需出网" : "离线浏览");
      capBox.textContent = state.online ? bits.join(" · ") : "—";
    }
    renderRunSelect();
  }

  function renderRunSelect() {
    const select = $("cifRunSelect");
    const info = $("cifRunInfo");
    if (!select) return;
    if (!state.online) {
      select.innerHTML = '<option value="">（离线演示 · 无后端）</option>';
      select.disabled = true;
      if (info) info.textContent = "离线：runs 列表不可用（GET /api/runs 需要后端）";
      return;
    }
    select.disabled = !state.runs.length;
    if (!state.runs.length) {
      select.innerHTML = '<option value="">（还没有 run）</option>';
      if (info) info.textContent = "还没有 run：先跑一次管线或任一个 op；每个 op 新建一个 run 目录，不覆盖同名目录。";
      return;
    }
    select.innerHTML = state.runs.map((run) => {
      const bits = [run.rel || run.id];
      if (run.kind) bits.push(run.kind);
      if (run.n_artifacts != null) bits.push("产物 " + run.n_artifacts);
      if (run.formula) bits.push(run.formula);
      return '<option value="' + esc(run.rel || run.id) + '"' + ((run.rel || run.id) === state.currentRun ? " selected" : "") + ">" +
        esc(bits.join(" · ")) + "</option>";
    }).join("");
    const run = state.runs.find((item) => (item.rel || item.id) === state.currentRun) || state.runs[0];
    if (info) {
      if (!run) info.textContent = "—";
      else {
        const parts = [];
        parts.push(run.kind || "?");
        parts.push(run.ok === false ? "失败步 " + (run.failed_step || "?") : "ok");
        if (run.atoms != null) parts.push("atoms " + run.atoms);
        if (run.formula) parts.push(run.formula);
        if (run.topology) parts.push("topology " + run.topology);
        if (run.source) parts.push("source " + run.source);
        parts.push(run.has_report ? "有 report.md" : "无 report.md");
        if (run.has_preview) parts.push("有预览图");
        info.textContent = parts.join(" · ");
        info.title = run.abs || "";
      }
    }
  }

  async function refreshRuns(options = {}) {
    if (!state.online) { renderRunSelect(); return; }
    try {
      const data = await getJson(ENDPOINTS.runs);
      state.runs = Array.isArray(data.runs) ? data.runs : [];
      if (!state.currentRun || !state.runs.some((run) => (run.rel || run.id) === state.currentRun)) {
        state.currentRun = state.runs.length ? (state.runs[0].rel || state.runs[0].id) : null;
      }
      renderRunSelect();
      renderInputSource(currentScenario());
      if (state.currentRun) await loadSnapshot(state.currentRun, { quiet: true });
    } catch (error) {
      if (!options.quiet) toast("读取 runs 失败：" + error.message, "error");
      renderRunSelect();
    }
  }

  async function loadSnapshot(id, options = {}) {
    if (!state.online || !id) { state.snapshot = null; return null; }
    try {
      const data = await getJson(ENDPOINTS.run + "?id=" + encodeURIComponent(id));
      state.snapshot = data.run || null;
      state.currentRun = (state.snapshot && (state.snapshot.rel || state.snapshot.id)) || id;
      renderRunSelect();
      renderInputSource(currentScenario());
      return state.snapshot;
    } catch (error) {
      state.snapshot = null;
      if (!options.quiet) toast("读取 run 快照失败：" + error.message, "error");
      return null;
    }
  }

  async function refreshSources() {
    if (!state.online) { renderSources(); return; }
    try {
      const data = await getJson(ENDPOINTS.sources);
      state.sources = Array.isArray(data.sources) ? data.sources : [];
    } catch (error) {
      state.sources = [];
    }
    renderSources();
  }

  function renderSources() {
    const box = $("cifSources");
    if (!box) return;
    if (!state.online) { box.innerHTML = '<p class="cif-muted">离线：数据源状态不可用（GET /api/sources 需要后端）。</p>'; return; }
    if (!state.sources.length) { box.innerHTML = '<p class="cif-muted">后端没有返回 sources（不编造）。</p>'; return; }
    box.innerHTML = state.sources.map((source) => {
      const tone = source.ready ? "is-ok" : "is-warn";
      return '<span class="cif-source ' + tone + '" title="' + esc(source.detail || "") + '">' +
        esc(source.label || source.id) +
        '<small>' + esc(source.status || "unknown") + '</small>' +
        (source.hint ? '<em>' + esc(source.hint) + '</em>' : "") +
        '</span>';
    }).join("");
  }

  /* ------------------------------------------------------------------ 表单 */

  function defaultFormValues(scenario) {
    const values = {};
    (scenario.form || []).forEach((field) => {
      if (field.kind === "checkbox") values[field.name] = Boolean(field.default);
      else if (field.kind === "number") values[field.name] = field.default == null ? "" : field.default;
      else if (field.kind === "multiselect") values[field.name] = Array.isArray(field.defaults) ? field.defaults.slice() : [];
      else if (field.kind === "triple") values[field.name] = Array.isArray(field.defaults) ? field.defaults.slice() : ["", "", ""];
      else values[field.name] = field.default == null ? "" : field.default;
    });
    return values;
  }

  function fieldVisible(field) {
    if (!field.when) return true;
    return state.formValues.op === field.when;
  }

  function optionsFor(field) {
    if (field.options) return field.options;
    if (field.optionsFrom === "formats") {
      return Object.keys(state.formats || {}).map((name) => ({ value: name, label: name + (state.formats[name] ? " · " + String(state.formats[name]).slice(0, 40) : "") }));
    }
    if (field.optionsFrom === "bundles") {
      return Object.keys(state.bundles || {}).map((name) => ({ value: name, label: name + (state.bundles[name] ? " · " + String(state.bundles[name]).slice(0, 40) : "") }));
    }
    return [];
  }

  function fieldHtml(field, value) {
    const id = "cifField_" + field.name;
    const wide = field.wide || field.kind === "textarea" || field.kind === "multiselect" ? " cif-field-wide" : "";
    const help = field.help ? '<small class="cif-help">' + esc(field.help) + "</small>" : "";

    if (field.kind === "checkbox") {
      return '<label class="cif-field cif-field-bool' + wide + '"><input type="checkbox" id="' + id + '" data-field="' + esc(field.name) + '"' +
        (value ? " checked" : "") + '><span>' + esc(field.label) + '</span>' + help + "</label>";
    }
    if (field.kind === "number") {
      return '<label class="cif-field' + wide + '"><span>' + esc(field.label) + '</span><input class="input" type="number" id="' + id +
        '" data-field="' + esc(field.name) + '" value="' + esc(value == null ? "" : value) + '"' +
        (field.min != null ? ' min="' + field.min + '"' : "") + (field.max != null ? ' max="' + field.max + '"' : "") +
        (field.step != null ? ' step="' + field.step + '"' : "") + ' placeholder="' + esc(field.placeholder || "") + '">' + help + "</label>";
    }
    if (field.kind === "triple") {
      const list = Array.isArray(value) ? value : ["", "", ""];
      const labels = ["a", "b", "c"];
      return '<div class="cif-field' + wide + '"><span>' + esc(field.label) + '</span><div class="cif-triple">' +
        labels.map((name, index) => '<label><small>' + name + '</small><input class="input" type="number" data-field="' + esc(field.name) +
          '" data-triple="' + index + '" value="' + esc(list[index] == null ? "" : list[index]) + '"' +
          (field.step != null ? ' step="' + field.step + '"' : "") + (field.min != null ? ' min="' + field.min + '"' : "") + "></label>").join("") +
        "</div>" + help + "</div>";
    }
    if (field.kind === "select") {
      const options = optionsFor(field).map((item) => '<option value="' + esc(item.value) + '"' +
        (String(item.value) === String(value == null ? "" : value) ? " selected" : "") + ">" + esc(item.label) + "</option>").join("");
      return '<label class="cif-field' + wide + '"><span>' + esc(field.label) + '</span><select class="input" id="' + id +
        '" data-field="' + esc(field.name) + '">' + options + "</select>" + help + "</label>";
    }
    if (field.kind === "multiselect") {
      const chosen = Array.isArray(value) ? value : [];
      const options = optionsFor(field);
      const body = options.length
        ? options.map((item) => '<label class="cif-check"><input type="checkbox" data-field="' + esc(field.name) +
            '" data-multi="' + esc(item.value) + '"' + (chosen.includes(item.value) ? " checked" : "") + '><span>' + esc(item.label) + "</span></label>").join("")
        : '<p class="cif-muted">清单不可用（既没有 bootstrap 也没有本地注册表）——不编造格式清单。</p>';
      return '<div class="cif-field' + wide + '"><span>' + esc(field.label) + '</span><div class="cif-multiselect">' + body + "</div>" + help + "</div>";
    }
    if (field.kind === "textarea") {
      return '<label class="cif-field' + wide + '"><span>' + esc(field.label) + '</span><textarea class="textarea" id="' + id +
        '" data-field="' + esc(field.name) + '" rows="' + (field.rows || 3) + '" placeholder="' + esc(field.placeholder || "") + '">' +
        esc(value == null ? "" : value) + "</textarea>" + help + "</label>";
    }
    return '<label class="cif-field' + wide + '"><span>' + esc(field.label) + '</span><input class="input" type="text" id="' + id +
      '" data-field="' + esc(field.name) + '" value="' + esc(value == null ? "" : value) + '" placeholder="' + esc(field.placeholder || "") + '">' + help + "</label>";
  }

  function renderForm(scenario) {
    const box = $("cifFormFields");
    const block = $("cifForm");
    if (!box || !block) return;
    block.hidden = false;
    const fields = scenario.form || [];
    box.innerHTML = fields.filter(fieldVisible).map((field) => fieldHtml(field, state.formValues[field.name])).join("");
    const hint = $("cifFormHint");
    if (hint) hint.textContent = scenario.backend || "";
    refreshIcons();
  }

  function collectForm() {
    const box = $("cifFormFields");
    if (!box) return;
    const scenario = currentScenario();
    const values = state.formValues;
    (scenario.form || []).forEach((field) => {
      if (field.kind === "triple") {
        const inputs = box.querySelectorAll('[data-field="' + field.name + '"][data-triple]');
        if (!inputs.length) return;
        values[field.name] = Array.from(inputs).map((input) => (input.value === "" ? "" : Number(input.value)));
        return;
      }
      if (field.kind === "multiselect") {
        const inputs = box.querySelectorAll('[data-field="' + field.name + '"][data-multi]');
        if (!inputs.length) return;
        values[field.name] = Array.from(inputs).filter((input) => input.checked).map((input) => input.dataset.multi);
        return;
      }
      const control = box.querySelector('[data-field="' + field.name + '"]');
      if (!control) return;
      if (control.type === "checkbox") values[field.name] = control.checked;
      else if (control.type === "number") values[field.name] = control.value === "" ? "" : Number(control.value);
      else values[field.name] = control.value;
    });
  }

  function opParams(scenario) {
    // 只把**该 op 白名单内**的键发出去（CONTRACT §5；后端对未知字段返回 422）。
    const v = state.formValues;
    if (scenario.key === "repair") {
      return { add_h: Boolean(v.add_h), disorder: Boolean(v.disorder), contacts: Boolean(v.contacts) };
    }
    if (scenario.key === "geometry") {
      const params = {};
      if (v.standardize) params.standardize = true;
      const sc = Array.isArray(v.supercell) ? v.supercell.map((n) => Number(n || 1)) : [1, 1, 1];
      if (sc.some((n) => Number.isFinite(n) && n > 1)) params.supercell = sc;
      const surface = Array.isArray(v.surface) ? v.surface : [];
      if (surface.length === 3 && surface.every((n) => n !== "" && n != null)) params.surface = surface.map(Number);
      if (v.surface_frame) params.surface_frame = String(v.surface_frame);
      if (v.vacuum !== "" && v.vacuum != null) params.vacuum = Number(v.vacuum);
      if (v.thickness !== "" && v.thickness != null) params.thickness = Number(v.thickness);
      if (v.cluster) params.cluster = String(v.cluster);
      return params;
    }
    if (scenario.key === "export") {
      const params = {};
      if (Array.isArray(v.formats) && v.formats.length) params.formats = v.formats.slice();
      if (Array.isArray(v.bundles) && v.bundles.length) params.bundles = v.bundles.slice();
      if (v.stem) params.stem = String(v.stem);
      if (v.title) params.title = String(v.title);
      return params;
    }
    if (scenario.key === "inspect") {
      const op = v.op || "validate";
      if (op === "features") {
        const params = {};
        params.rdf = Boolean(v.rdf);
        if (v.rdf_bins !== "" && v.rdf_bins != null) params.rdf_bins = Number(v.rdf_bins);
        if (v.rdf_max !== "" && v.rdf_max != null) params.rdf_max = Number(v.rdf_max);
        params.topology = Boolean(v.topology);
        params.rings = Boolean(v.rings);
        return params;
      }
      if (op === "topology") return v.net ? { net: String(v.net) } : {};
      return {};
    }
    if (scenario.key === "library") {
      const params = {};
      if (v.from_runs) params.from_runs = true;
      if (v.from_library) params.from_library = true;
      if (v.label_key) params.label_key = String(v.label_key);
      if (v.no_features) params.no_features = true;
      if (v.no_structures) params.no_structures = true;
      if (v.dedupe) params.dedupe = String(v.dedupe);
      if (v.jobs !== "" && v.jobs != null) params.jobs = Number(v.jobs);
      return params;
    }
    return {};
  }

  function scenarioOp(scenario) {
    if (scenario.key === "export") return "convert";     // CONTRACT §3.3：export 场景背后是 convert
    if (scenario.key === "inspect") return state.formValues.op || "validate";
    if (scenario.key === "library") return "dataset";
    if (scenario.key === "repair") return "repair";
    if (scenario.key === "geometry") return "geom";
    return null;
  }

  /* ------------------------------------------------------------------ 输入来源 */

  function renderInputSource(scenario) {
    const block = $("cifInputSource");
    if (!block) return;
    if (!scenario.needsInput) { block.hidden = true; return; }
    block.hidden = false;

    const snapshot = state.snapshot;
    const artifacts = (snapshot && Array.isArray(snapshot.artifacts)) ? snapshot.artifacts : [];
    const pattern = /\.(cif|vasp|poscar|xyz|extxyz|pdb|json|lammps|data|qe|in|cp2k|gjf|orca|xsf|gro|top|cfg|gen|sdf|mol)$/i;
    const structureOnes = artifacts.filter((item) => pattern.test(item.name || ""));
    if (state.inputMode === "run" && !structureOnes.length) state.inputMode = "cif_text";

    const modeHtml = [
      { value: "run", label: "已有 run 的产物", disabled: !structureOnes.length },
      { value: "cif_text", label: "粘贴 CIF 文本", disabled: false },
      { value: "path", label: "服务端路径", disabled: false },
    ].map((mode) => '<label class="cif-mode' + (state.inputMode === mode.value ? " is-on" : "") + (mode.disabled ? " is-off" : "") + '">' +
      '<input type="radio" name="cifInputMode" value="' + mode.value + '"' + (state.inputMode === mode.value ? " checked" : "") +
      (mode.disabled ? " disabled" : "") + "><span>" + esc(mode.label) + "</span></label>").join("");

    let body = "";
    if (state.inputMode === "run") {
      const options = structureOnes.map((item) => '<option value="' + esc(item.name) + '"' +
        (item.name === state.inputArtifact ? " selected" : "") + ">" + esc(item.name) + " · " + esc(humanBytes(item.bytes)) + "</option>").join("");
      body = '<label class="cif-field cif-field-wide"><span>从 run <code>' + esc(state.currentRun || "—") + '</code> 里取哪个产物</span>' +
        '<select class="input" id="cifInputArtifact">' + options + "</select>" +
        '<small class="cif-help">POST /api/op 的 input 用 <code>{"run":"…","name":"…"}</code>（CONTRACT §5 第 1 种）。</small></label>';
    } else if (state.inputMode === "path") {
      body = '<label class="cif-field cif-field-wide"><span>服务端路径（必须在 runs-dir 或 --allow-import-root 内）</span>' +
        '<input class="input" type="text" id="cifInputPath" value="' + esc(state.inputPath) + '" placeholder="例如 state\\runs\\demo\\structure.cif">' +
        '<small class="cif-help">越界会返回 <code>400 E_PATH_OUTSIDE</code>（不是静默失败）。</small></label>';
    } else {
      body = '<label class="cif-field cif-field-wide"><span>粘贴 CIF 文本（≤ limits.max_upload_bytes）</span>' +
        '<textarea class="textarea" id="cifInputCif" rows="6" spellcheck="false" placeholder="data_demo&#10;_cell_length_a 10.0&#10;…">' +
        esc(state.inputCif) + "</textarea>" +
        '<small class="cif-help">整段 CIF 作为 <code>input.cif_text</code> 发给 <code>POST /api/op</code>；本页不做解析。</small></label>';
    }

    block.innerHTML = '<div class="cif-block-head"><span class="kicker">输入来源</span>' +
      '<span class="hint">三选一：已有 run 的产物 / 粘贴 CIF / 服务端路径（CONTRACT §5）</span></div>' +
      '<div class="cif-modes">' + modeHtml + "</div>" + body;
    refreshIcons();
  }

  function buildInput() {
    const scenario = currentScenario();
    if (!scenario.needsInput) return null;
    if (state.inputMode === "path" && state.inputPath.trim()) return { path: state.inputPath.trim() };
    if (state.inputMode === "run" && state.currentRun && state.inputArtifact) {
      return { run: state.currentRun, name: state.inputArtifact };
    }
    if (state.inputCif.trim()) return { cif_text: state.inputCif };
    return null;
  }

  /* ------------------------------------------------------------------ 结果卡片 */

  function artifactUrl(runRel, name, download) {
    const params = new URLSearchParams();
    params.set("run", runRel);
    params.set("name", name);
    if (download) params.set("download", "1");
    return ENDPOINTS.artifact + "?" + params.toString();
  }

  function summaryRows(items) {
    return items.filter((row) => row && row[1] != null && row[1] !== "").map((row) =>
      '<div class="cif-kv"><dt>' + esc(row[0]) + '</dt><dd>' + esc(row[1]) + "</dd></div>").join("");
  }

  function validationHtml(validation) {
    if (!validation || !Array.isArray(validation.checks) || !validation.checks.length) {
      return '<p class="cif-muted">这个 run 没有 validation 记录（不编造校验结论）。</p>';
    }
    const rows = validation.checks.map((check) => {
      const ok = check.ok !== false;
      return '<li class="cif-check-row ' + (ok ? "is-ok" : "is-bad") + '">' +
        '<span class="cif-check-mark">' + (ok ? "✓" : "✗") + "</span>" +
        "<strong>" + esc(check.name || "?") + "</strong>" +
        '<span class="cif-check-detail">' + esc(check.detail || "") + "</span>" +
        (check.severity ? '<span class="cif-sev">' + esc(check.severity) + "</span>" : "") +
        "</li>";
    }).join("");
    return '<ul class="cif-checks">' + rows + "</ul>" +
      '<p class="cif-muted">整表结论：' + (validation.ok === false ? "不通过" : "通过") + "</p>";
  }

  function productsHtml(items, options = {}) {
    if (!Array.isArray(items) || !items.length) return '<p class="cif-muted">没有产物（不编造文件列表）。</p>';
    const offline = Boolean(options.offline);
    const runRel = options.runRel || "";
    const rows = items.map((item) => {
      const name = item.name || item.rel || "?";
      const kind = item.kind || "";
      const isImage = /\.(png|jpe?g|webp|gif)$/i.test(name) || kind === "image";
      const href = offline ? "" : artifactUrl(runRel, name, true);
      const previewHref = offline ? "" : artifactUrl(runRel, name, false);
      return '<li class="cif-product' + (isImage ? " is-image" : "") + '">' +
        '<span class="cif-prod-name"><i data-lucide="' + (isImage ? "image" : (kind === "json" ? "braces" : "file-text")) + '"></i>' + esc(name) + "</span>" +
        '<span class="cif-prod-meta">' + esc(kind || "—") + " · " + esc(humanBytes(item.bytes)) + "</span>" +
        (offline
          ? '<span class="cif-prod-actions"><span class="cif-muted" title="离线演示：没有真实文件，不提供下载">下载（离线无产物）</span></span>'
          : '<span class="cif-prod-actions">' +
              '<a class="btn btn-ghost btn-sm" href="' + esc(href) + '" download>' +
              '<i data-lucide="download"></i>下载</a>' +
              (isImage ? '<a class="btn btn-ghost btn-sm" href="' + esc(previewHref) + '" target="_blank" rel="noopener">' +
                '<i data-lucide="external-link"></i>打开预览</a>' : "") +
            "</span>") +
        "</li>";
    }).join("");
    return '<ul class="cif-products">' + rows + "</ul>";
  }

  function previewHtml(items, options = {}) {
    const list = Array.isArray(items) ? items : [];
    const image = list.find((item) => /\.(png|jpe?g|webp|gif)$/i.test(item.name || "") || item.kind === "image");
    if (!image) return '<p class="cif-muted">这次没有 PNG 预览（cifkit.render 生成 preview.png 时才显示；不伪造图）。</p>';
    if (options.offline) {
      return '<div class="cif-preview is-offline"><div class="cif-preview-ph"><i data-lucide="image-off"></i>' +
        "<span>离线演示：没有真实预览图</span></div><small>后端在线时这里是 <code>" + esc(image.name) + "</code> 的真实 PNG。</small></div>";
    }
    const src = artifactUrl(options.runRel || "", image.name, false);
    return '<figure class="cif-preview"><img src="' + esc(src) + '" alt="结构预览 ' + esc(image.name) + '" loading="lazy">' +
      "<figcaption>" + esc(image.name) + " · " + esc(humanBytes(image.bytes)) + "</figcaption></figure>";
  }

  function reportHtml(reportMd, options = {}) {
    if (!reportMd) {
      return '<p class="cif-muted">这个 run 没有 report.md（外部目录或单步 op run）；不编造报告。</p>';
    }
    try {
      const html = window.marked ? window.marked.parse(reportMd, { gfm: true, breaks: true }) : esc(reportMd).replace(/\n/g, "<br>");
      const clean = window.DOMPurify ? window.DOMPurify.sanitize(html) : html;
      return '<div class="cif-report md">' + clean + "</div>";
    } catch (error) {
      return '<pre class="cif-report-raw">' + esc(reportMd) + "</pre>";
    }
  }

  function nextHtml(scenario) {
    const list = scenario.next || [];
    if (!list.length) return "";
    return '<div class="cif-next"><span class="kicker">下一步</span>' +
      list.map((item) => '<button class="btn btn-ghost btn-sm" type="button" data-cif-goto="' + esc(item.key) + '">' +
        esc(item.label) + '<i data-lucide="arrow-right"></i></button>').join("") + "</div>";
  }

  function resultCard(o) {
    const box = $("cifResult");
    if (!box) return;
    const scenario = currentScenario();
    box.hidden = false;
    box.innerHTML =
      '<div class="cif-card' + (o.offline ? " is-offline" : "") + '">' +
        '<div class="cif-card-head">' +
          '<span class="kicker">' + esc(o.title || "结果") + '</span>' +
          (o.offline
            ? '<span class="cif-badge is-offline">离线演示（无后端）· 预置 mock</span>'
            : '<span class="cif-badge is-ok">结构化返回</span>') +
          (o.runLabel ? '<span class="cif-badge">' + esc(o.runLabel) + "</span>" : "") +
        "</div>" +
        (o.offline
          ? '<p class="cif-offline-note"><i data-lucide="alert-triangle"></i>' +
            esc(o.note || "这是 data.js 里的预置 mock，不是 cifkit 的真实输出；起后端后同一个按钮会打真实端点。") + "</p>"
          : (o.note ? '<p class="cif-note">' + esc(o.note) + "</p>" : "")) +
        (o.headerExtra || "") +
        '<div class="cif-card-grid">' +
          '<section class="cif-sec"><h4><i data-lucide="ruler"></i>结构数字</h4><dl class="cif-kv-list">' +
            summaryRows(o.summary || []) + "</dl></section>" +
          '<section class="cif-sec"><h4><i data-lucide="list-checks"></i>校验清单</h4>' + validationHtml(o.validation) + "</section>" +
        "</div>" +
        '<section class="cif-sec"><h4><i data-lucide="package"></i>产物列表</h4>' +
          productsHtml(o.products, { offline: o.offline, runRel: o.runRel }) + "</section>" +
        '<section class="cif-sec"><h4><i data-lucide="image"></i>PNG 预览</h4>' +
          previewHtml(o.products, { offline: o.offline, runRel: o.runRel }) + "</section>" +
        '<section class="cif-sec"><h4><i data-lucide="file-text"></i>Markdown 报告</h4>' +
          reportHtml(o.report, o) + "</section>" +
        (o.warnings && o.warnings.length
          ? '<section class="cif-sec"><h4><i data-lucide="triangle-alert"></i>warnings</h4><ul class="cif-warnings">' +
            o.warnings.map((w) => "<li>" + esc(w) + "</li>").join("") + "</ul></section>"
          : "") +
        (o.bodyExtra || "") +
        nextHtml(scenario) +
      "</div>";
    refreshIcons();
  }

  function snapshotSummary(snapshot, opResult) {
    const rows = [];
    rows.push(["run", snapshot.rel || snapshot.id || "?"]);
    rows.push(["kind", snapshot.kind || "?"]);
    if (snapshot.atoms != null) rows.push(["原子数 atoms", String(snapshot.atoms)]);
    if (snapshot.formula) rows.push(["组成 formula", snapshot.formula]);
    if (snapshot.topology) rows.push(["拓扑 topology", snapshot.topology]);
    if (opResult && opResult.natoms != null) rows.push(["原子数（op 返回）", String(opResult.natoms)]);
    if (opResult && opResult.formula) rows.push(["组成（op 返回）", opResult.formula]);
    if (opResult && Array.isArray(opResult.abc)) rows.push(["晶胞 abc (Å)", opResult.abc.map((n) => Number(n).toFixed(3)).join(" / ")]);
    if (snapshot.n_steps != null) rows.push(["步骤 n_steps", String(snapshot.n_steps)]);
    if (Array.isArray(snapshot.artifacts)) rows.push(["产物 n_artifacts", String(snapshot.artifacts.length)]);
    if (snapshot.source) rows.push(["数据源 source", snapshot.source]);
    if (snapshot.failed_step) rows.push(["失败步骤 failed_step", snapshot.failed_step]);
    return rows;
  }

  function renderSnapshotResult(snapshot, opResult, extras = {}) {
    const scenario = currentScenario();
    resultCard({
      title: (extras.title || scenario.title) + " · 结果卡片",
      offline: false,
      runLabel: snapshot.rel || snapshot.id,
      note: extras.note || "",
      summary: snapshotSummary(snapshot, opResult),
      validation: snapshot.validation || null,
      products: Array.isArray(snapshot.artifacts) && snapshot.artifacts.length ? snapshot.artifacts : (extras.products || []),
      report: snapshot.report_md || null,
      warnings: Array.isArray(snapshot.warnings) ? snapshot.warnings : [],
      runRel: snapshot.rel || snapshot.id || "",
      bodyExtra: extras.bodyExtra || "",
      headerExtra: extras.headerExtra || "",
    });
  }

  function renderMockResult(scenario, reason) {
    const mock = scenario.mockResult || {};
    resultCard({
      title: (scenario.title || "结果") + " · 离线演示",
      offline: true,
      note: reason || mock.note,
      runLabel: "无后端 · mock",
      summary: mock.summary || [],
      validation: mock.validation || null,
      products: mock.products || [],
      report: mock.report || null,
      warnings: ["离线演示：以上数字与文件全部是 data.js 的预置 mock，不是 cifkit 的输出，也不能下载。"],
      runRel: "",
    });
  }

  /* ------------------------------------------------------------------ 作业日志（SSE） */

  function renderJobLog() {
    const box = $("cifJobLog");
    if (!box) return;
    const job = state.lastJob;
    const lines = state.logLines;
    const steps = Object.keys(state.jobSteps).map((key) => state.jobSteps[key]).sort((a, b) => Number(a.index) - Number(b.index));
    const running = job && (job.status === "running" || job.status === "queued" || job.status === "stopping");
    const stepsHtml = steps.length
      ? '<ol class="cif-steps">' + steps.map((step) =>
          '<li class="is-' + esc(step.status || "pending") + '"><span class="cif-step-no">' + esc(step.index) + "</span>" +
          "<strong>" + esc(step.name || "?") + "</strong>" +
          '<span class="cif-step-status">' + esc(step.status || "") + "</span>" +
          (step.detail ? '<small>' + esc(step.detail) + "</small>" : "") + "</li>").join("") + "</ol>"
      : '<p class="cif-muted">还没有 step 事件。</p>';
    box.hidden = false;
    box.innerHTML =
      '<div class="cif-card cif-card-log">' +
        '<div class="cif-card-head"><span class="kicker">作业日志 · SSE /api/job/events</span>' +
          (job ? '<span class="cif-badge">' + esc(job.id || "?") + " · " + esc(job.status || "?") + "</span>" : "") +
          (running ? '<button class="btn btn-accent btn-sm" type="button" id="cifCancelJob"><i data-lucide="square"></i>取消作业</button>' : "") +
        "</div>" +
        '<p class="cif-note">' + (job
          ? "kind=" + esc(job.kind || "?") + " · 已过 " + esc(job.elapsed_s == null ? "?" : job.elapsed_s) + " s · 日志 " + lines.length + " 行（子进程原文，不加工）"
          : "还没有启动作业。") + "</p>" +
        stepsHtml +
        '<pre class="cif-log" id="cifLogBody">' + esc(lines.slice(-400).join("\n")) + "</pre>" +
      "</div>";
    const body = $("cifLogBody");
    if (body) body.scrollTop = body.scrollHeight;
    refreshIcons();
  }

  function openJobStream(jobId) {
    closeJobStream();
    state.logLines = [];
    state.jobSteps = {};
    state.lastJob = { id: jobId, status: "running", kind: "pipeline" };
    renderJobLog();

    let es;
    try {
      es = new EventSource(ENDPOINTS.jobEvents + "?id=" + encodeURIComponent(jobId));
    } catch (error) {
      toast("EventSource 不可用：" + error.message, "error");
      return;
    }
    state.es = es;

    es.addEventListener("meta", (event) => {
      const data = safeJson(event.data);
      if (data) {
        state.lastJob = Object.assign({}, state.lastJob, { job_id: data.job_id, kind: data.kind || state.lastJob.kind });
        if (data.argv) state.lastJob.argv = data.argv;
      }
      renderJobLog();
    });
    es.addEventListener("log", (event) => {
      const data = safeJson(event.data);
      if (!data) return;
      state.logLines.push((data.stream === "stderr" ? "[stderr] " : "") + (data.line == null ? "" : data.line));
      const body = $("cifLogBody");
      if (body) {
        body.textContent = state.logLines.slice(-400).join("\n");
        body.scrollTop = body.scrollHeight;
      }
    });
    es.addEventListener("step", (event) => {
      const data = safeJson(event.data);
      if (!data) return;
      state.jobSteps[String(data.index)] = data;
      renderJobLog();
    });
    es.addEventListener("notice", (event) => {
      const data = safeJson(event.data);
      if (data && data.message) toast(data.message);
    });
    es.addEventListener("error", (event) => {
      // EventSource 的 error 事件既可能是服务端的 error 帧，也可能是连接中断。
      const data = event && event.data ? safeJson(event.data) : null;
      if (data && data.message) {
        toast("作业错误：" + data.message, "error");
        return;
      }
      if (state.es && state.es.readyState === 2) {
        toast("作业事件流已断开（作业可能仍在后端跑；可刷新页面重放日志）", "error");
      }
    });
    es.addEventListener("done", async (event) => {
      const data = safeJson(event.data) || {};
      state.lastJob = Object.assign({}, state.lastJob, {
        status: data.status || "done",
        returncode: data.returncode,
        elapsed_s: data.elapsed_s,
        run_dir: data.run_dir,
      });
      renderJobLog();
      closeJobStream();
      toast("作业结束：" + (data.status || "done") + " · returncode " + (data.returncode == null ? "?" : data.returncode));
      await refreshRuns({ quiet: true });
      if (data.run_dir) {
        const runRel = pickRunRelFor(data.run_dir);
        if (runRel) {
          const snapshot = await loadSnapshot(runRel, { quiet: true });
          if (snapshot) renderSnapshotResult(snapshot, null, { title: "管线结果" });
        }
      }
    });
  }

  function pickRunRelFor(runDir) {
    if (!runDir) return null;
    const abs = String(runDir).replace(/\\/g, "/");
    const hit = state.runs.find((run) => run.abs && String(run.abs).replace(/\\/g, "/") === abs);
    if (hit) return hit.rel || hit.id;
    const tail = abs.split("/").filter(Boolean).pop();
    const byTail = state.runs.find((run) => (run.rel || run.id) === tail);
    return byTail ? (byTail.rel || byTail.id) : tail || null;
  }

  function closeJobStream() {
    if (state.es) {
      try { state.es.close(); } catch (error) { /* ignore */ }
      state.es = null;
    }
  }

  function safeJson(text) {
    if (text == null || text === "") return null;
    try { return JSON.parse(text); } catch (error) { return null; }
  }

  async function cancelJob() {
    const job = state.lastJob;
    if (!job || !job.id) { toast("没有在跑的作业"); return; }
    try {
      const data = await postJson(ENDPOINTS.jobCancel, { id: job.id });
      state.lastJob = Object.assign({}, state.lastJob, data.job || {});
      toast("已请求取消：" + (data.job ? data.job.status : "cancelling"));
      renderJobLog();
    } catch (error) {
      const info = errorLine(error);
      toast("取消失败：" + info.code + " · " + info.message + (info.hint ? "（" + info.hint + "）" : ""), "error");
    }
  }

  /* ------------------------------------------------------------------ 运行：kickoff（route → job） */

  function planMarkdown(spec, plan, warnings) {
    const lines = [];
    lines.push("### 路由结果（POST /api/route，未执行）");
    lines.push("");
    if (spec) {
      lines.push("**TaskSpec**");
      lines.push("");
      lines.push(BQ3 + "json");
      lines.push(JSON.stringify(spec, null, 2));
      lines.push(BQ3);
    } else {
      lines.push("后端没有返回 spec（不编造）。");
    }
    lines.push("");
    lines.push("**执行计划**");
    lines.push("");
    if (Array.isArray(plan) && plan.length) {
      lines.push("| # | 步骤 | 可选 | 说明 |");
      lines.push("| --- | --- | --- | --- |");
      plan.forEach((step, index) => {
        lines.push("| " + (index + 1) + " | " + (step.name || "?") + " | " + (step.optional ? "是" : "否") + " | " + (step.description || "") + " |");
      });
    } else {
      lines.push("后端没有返回 plan（不编造）。");
    }
    if (Array.isArray(warnings) && warnings.length) {
      lines.push("");
      lines.push("**warnings**");
      warnings.forEach((warning) => lines.push("- " + warning));
    }
    return lines.join("\n");
  }

  async function kickoffRoute(text, emit) {
    const stages = currentScenario().stages || [];
    const blocked = writeBlocked();
    if (blocked) {
      showErrorCard("只读模式（未加 --allow-exec）· 前端先自查，不发注定 403 的请求", blocked);
      emit({ type: "error", message: blocked.message, hint: blocked.hint });
      return;
    }
    if (!text) {
      showErrorCard("缺少一句话请求（前端先自查，不发注定被拒的请求）", {
        code: "E_BAD_REQUEST",
        message: "请先在上面的表单里写一句要做什么",
        hint: 'POST /api/route 需要 {"text":"…"}',
      });
      emit({ type: "error", message: "缺少一句话请求" });
      return;
    }
    emit({ type: "stage", index: 0, label: stages[0], status: "running" });
    let data;
    try {
      data = await postJson(ENDPOINTS.route, { text: text });
    } catch (error) {
      emit({ type: "error", message: (error.code || "E_") + " " + error.message, hint: error.hint || "" });
      showErrorCard("路由失败（POST /api/route）", error);
      return;
    }
    const spec = data.spec || null;
    const plan = Array.isArray(data.plan) ? data.plan : [];
    const warnings = Array.isArray(data.warnings) ? data.warnings : [];
    state.pendingSpec = { text, spec, plan, warnings };

    emit({ type: "stage", index: 0, label: stages[0], status: "ok" });
    emit({ type: "stage", index: 1, label: stages[1], status: "ok" });
    emit({ type: "delta", text: planMarkdown(spec, plan, warnings) + "\n\n> 计划由人确认；确认后才会 POST /api/job 起 pipeline。\n" });

    resultCard({
      title: "路由结果 · 计划（未执行）",
      offline: false,
      runLabel: "POST /api/route",
      note: "这一步只路由、不执行：没有起子进程，也没有写 run 目录。",
      summary: [
        ["源 source", spec && (spec.source || spec.source_id)],
        ["拓扑 topology", spec && spec.topology],
        ["结构 structure_id", spec && (spec.structure_id || spec.name)],
        ["步骤数 plan", plan.length ? String(plan.length) : null],
        ["warnings", String(warnings.length)],
      ],
      validation: null,
      products: [],
      report: planMarkdown(spec, plan, warnings),
      warnings: warnings,
      bodyExtra:
        '<div class="cif-confirm">' +
          '<div><span class="kicker">确认后开工</span>' +
            '<p class="cif-muted">点下面按钮 → <code>POST /api/job {kind:"pipeline"}</code> → 页面用 EventSource 订阅 ' +
            '<code>/api/job/events?id=</code> 实时打日志 + 步骤条 + 取消按钮。</p></div>' +
          '<button class="btn btn-accent" type="button" id="cifStartJob"><i data-lucide="play"></i>确认开工（起 pipeline）</button>' +
        "</div>",
      runRel: "",
    });
  }

  async function startPipelineJob() {
    const pending = state.pendingSpec;
    if (!pending) { toast("先做一次路由预览"); return; }
    const button = $("cifStartJob");
    if (button) button.disabled = true;
    try {
      // CONTRACT §9.2（v1.1 冻结）：POST /api/job = {kind, payload:{…}}，
      // payload 只认 query/spec/source/topology/formats/surface_frame/bundles/ingest/outdir，
      // 且 query 与 spec **互斥**（同时给 → 422），未知键 → 422。
      // 这里优先用 route 预览出来的 spec（这就是"先预览再确认"的意义）；没有 spec 才退回 query。
      const jobPayload = pending.spec ? { spec: pending.spec } : { query: pending.text };
      const body = { kind: "pipeline", payload: jobPayload };
      const data = await postJson(ENDPOINTS.job, body);
      const job = data.job || {};
      state.lastJob = job;
      openJobStream(job.id);
      toast("作业已启动：" + job.id);
      renderJobLog();
      const block = $("cifJobBlock");
      if (block) block.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch (error) {
      const info = errorLine(error);
      showErrorCard("启动作业失败（POST /api/job）", error);
      toast("启动作业失败：" + info.code + " · " + info.message, "error");
    } finally {
      if (button) button.disabled = false;
    }
  }

  /* ------------------------------------------------------------------ 运行：op 场景 */

  async function runOp(payload, emit) {
    const scenario = currentScenario();
    const op = scenarioOp(scenario);
    const stages = scenario.stages || [];
    const body = { op: op, params: opParams(scenario), run: null, input: buildInput() };
    const box = $("cifResult");

    const blocked = writeBlocked();
    if (blocked) {
      showErrorCard("只读模式（未加 --allow-exec）· 前端先自查，不发注定 403 的请求", blocked);
      emit({ type: "error", message: blocked.message, hint: blocked.hint });
      return;
    }
    if (scenario.needsInput && !body.input) {
      showErrorCard("缺少输入（前端先自查，不发注定被拒的请求）", {
        code: "E_BAD_REQUEST",
        message: "没有可用的结构输入：请选一个 run 产物、粘贴 CIF，或填一个服务端路径",
        hint: "CONTRACT §5：input 三选一（run+name / path / cif_text）",
      });
      emit({ type: "error", message: "缺少输入：请先选/填一个结构输入", hint: "input 三选一" });
      return;
    }
    if (scenario.key === "export" && !(body.params.formats || []).length && !(body.params.bundles || []).length) {
      showErrorCard("缺少导出目标（前端先自查）", {
        code: "E_BAD_REQUEST",
        message: "至少选一个格式或一个计算输入包",
        hint: "CONTRACT §5：convert 的 params.formats / params.bundles",
      });
      emit({ type: "error", message: "至少选一个格式或输入包" });
      return;
    }

    if (box) { box.hidden = false; box.innerHTML = jobPlaceholder(op, body); refreshIcons(); }

    for (let index = 0; index < stages.length; index += 1) {
      emit({ type: "stage", index: index, label: stages[index], status: index === 0 ? "running" : "pending" });
    }

    const started = performance.now();
    let data;
    try {
      data = await postJson(ENDPOINTS.op, body);
    } catch (error) {
      emit({ type: "error", message: (error.code || "E_") + " " + error.message, hint: error.hint || "" });
      showErrorCard("执行失败（POST /api/op · " + op + "）", error,
        '<p class="cif-muted">失败也会落 <code>op.json</code>（ok:false + stderr 原文），可用 runs 下拉里的 run 复盘。</p>');
      await refreshRuns({ quiet: true });
      return;
    }
    const elapsed = Math.round(performance.now() - started);

    // 阶段收尾：op 是同步调用，返回即说明各阶段都做完了（不为观感编造中间耗时）。
    for (let index = 0; index < stages.length; index += 1) {
      emit({ type: "stage", index: index, label: stages[index], status: "ok" });
    }

    const runRel = (data.run && (data.run.rel || data.run.id)) || null;
    if (runRel) state.currentRun = runRel;
    let snapshot = null;
    if (runRel) snapshot = await loadSnapshot(runRel, { quiet: true });

    const fallbackReport = opResultMarkdown(op, body.params, data);
    const report = (snapshot && snapshot.report_md) || fallbackReport;
    emit({ type: "delta", text: report + "\n" });

    // 权威结果取结构化返回（CONTRACT §5.2）：result / products / run / elapsed_ms / argv / warnings。
    const products = Array.isArray(data.products) && data.products.length
      ? data.products
      : (snapshot && Array.isArray(snapshot.artifacts) ? snapshot.artifacts : []);
    const summary = [];
    summary.push(["op", op]);
    if (data.run) {
      summary.push(["run", data.run.rel || data.run.id]);
      summary.push(["run kind", data.run.kind || "op"]);
    }
    const opResult = data.result || {};
    Object.keys(opResult).forEach((key) => {
      const value = opResult[key];
      if (value == null) return;
      if (typeof value === "object") {
        if (Array.isArray(value) && value.every((item) => typeof item === "number")) {
          summary.push([key, value.map((n) => Number(n).toFixed(3)).join(" / ")]);
        } else if (Array.isArray(value)) {
          summary.push([key, value.length + " 项"]);
        } else {
          summary.push([key, JSON.stringify(value).slice(0, 160)]);
        }
        return;
      }
      summary.push([key, String(value)]);
    });
    if (snapshot && snapshot.atoms != null) summary.push(["atoms（快照）", String(snapshot.atoms)]);
    if (data.elapsed_ms != null) summary.push(["elapsed_ms（后端）", String(Math.round(data.elapsed_ms))]);
    if (Array.isArray(data.argv)) summary.push(["argv", data.argv.join(" ")]);

    const unionProducts = unionBy(products, snapshot && snapshot.artifacts ? snapshot.artifacts : [], "name");

    resultCard({
      title: scenario.title + " · 结果卡片",
      offline: false,
      runLabel: runRel || "op run",
      note: "权威结果取 POST /api/op 的结构化返回；校验清单与报告来自 run 快照（manifest / report.md）。",
      summary: summary,
      validation: (snapshot && snapshot.validation) || (data.validation || null),
      products: unionProducts,
      report: report,
      warnings: (Array.isArray(data.warnings) ? data.warnings : []).concat(snapshot && Array.isArray(snapshot.warnings) ? snapshot.warnings : []),
      runRel: runRel || "",
      bodyExtra: '<p class="cif-muted">实际调用：<code>' + esc((data.argv || []).join(" ") || ("POST /api/op " + op)) + "</code> · 端到端 " + elapsed + " ms（含前端往返）</p>",
    });
    toast("完成 · " + op + " · " + (runRel || "op run"));
    await refreshRuns({ quiet: true });
    if (runRel) await loadSnapshot(runRel, { quiet: true });
  }

  function unionBy(a, b, key) {
    const out = [];
    const seen = new Set();
    [...(a || []), ...(b || [])].forEach((item) => {
      const id = item && item[key];
      if (!id || seen.has(id)) return;
      seen.add(id);
      out.push(item);
    });
    return out;
  }

  function opResultMarkdown(op, params, data) {
    const lines = [];
    lines.push("### " + op + " · 结构化返回摘要");
    lines.push("");
    lines.push("> 这是前端按 POST /api/op 的结构化返回渲染的摘要；该 run 没有 report.md 时用它替代（不是流式报告）。");
    lines.push("");
    lines.push("**params**");
    lines.push("");
    lines.push(BQ3 + "json");
    lines.push(JSON.stringify(params || {}, null, 2));
    lines.push(BQ3);
    lines.push("");
    if (data && data.result) {
      lines.push("**result**");
      lines.push("");
      lines.push(BQ3 + "json");
      lines.push(JSON.stringify(data.result, null, 2).slice(0, 4000));
      lines.push(BQ3);
    }
    if (data && Array.isArray(data.products) && data.products.length) {
      lines.push("");
      lines.push("**products**");
      lines.push("");
      lines.push("| 文件 | 类型 | 字节 |");
      lines.push("| --- | --- | --- |");
      data.products.forEach((item) => lines.push("| " + item.name + " | " + (item.kind || "—") + " | " + (item.bytes == null ? "—" : item.bytes) + " |"));
    }
    return lines.join("\n");
  }

  function jobPlaceholder(op, body) {
    const inputDesc = body.input
      ? (body.input.run ? "run " + body.input.run + " / " + body.input.name
        : (body.input.path ? "path " + body.input.path : "cif_text（" + String(body.input.cif_text || "").length + " 字符）"))
      : "无（不需要输入）";
    return '<div class="cif-card is-busy"><div class="cif-card-head"><span class="kicker">执行中 · ' + esc(op) + '</span>' +
      '<span class="cif-badge">POST /api/op</span></div>' +
      '<p class="cif-note">正在调 cifkit（' + esc(inputDesc) + '）。这一屏不会自己写 run 目录：产物只由 cifkit 写。</p>' +
      '<pre class="cif-log">' + esc("POST /api/op\n" + JSON.stringify(body, null, 2)) + "</pre></div>";
  }

  /* ------------------------------------------------------------------ 离线 mock 执行 */

  async function mockFlow(payload, emit) {
    const scenario = currentScenario();
    const runId = "mock-" + Date.now().toString(36);
    const stages = scenario.stages || [];
    emit({ type: "meta", run_id: runId, mode: "mock", model: "offline-mock" });
    for (let index = 0; index < stages.length; index += 1) {
      emit({ type: "stage", index: index, label: stages[index], status: "running" });
      await new Promise((resolve) => setTimeout(resolve, 120));
      emit({ type: "stage", index: index, label: stages[index], status: "ok" });
    }
    const text = scenario.mock || "";
    const size = 180;
    for (let index = 0; index < text.length; index += size) {
      emit({ type: "delta", text: text.slice(index, index + size) });
      await new Promise((resolve) => setTimeout(resolve, 8));
    }
    emit({ type: "notice", message: "离线演示（无后端）：上面是 data.js 的预置 mock，不是 cifkit 的输出" });
    renderMockResult(scenario, "后端不可达（GET /api/bootstrap 失败）：本卡片用 data.js 的预置 mock 渲染，" +
      "所有数字与文件都是演示占位，不能下载、也不代表任何真实结构结论。");
    emit({ type: "done", run_id: runId, elapsed_ms: 900, mode: "mock" });
  }

  /* ------------------------------------------------------------------ 接后端：只替换 EduAPI.run */

  function installAdapter() {
    if (!window.EduKit || !window.EduKit.EduAPI) {
      console.warn("[cif] 未找到 EduKit.EduAPI，请确认 assets/app.js 先于 cif.js 加载");
      return;
    }
    EduKit.EduAPI.run = async function cifRun(payload, emit) {
      const scenario = currentScenario();
      const box = $("cifResult");
      if (box) { box.hidden = true; box.innerHTML = ""; }
      const jobBlock = $("cifJobBlock");
      if (jobBlock) jobBlock.hidden = true;

      if (!state.online) {
        emit({ type: "notice", message: "后端不可达：这一屏走离线演示（预置 mock，不假装是真实结果）" });
        return mockFlow(payload, emit);
      }
      if (scenario.key === "kickoff") return kickoffRoute(String(state.formValues.text || "").trim(), emit);
      return runOp(payload, emit);
    };
  }

  /* ------------------------------------------------------------------ app.js 钩子 */

  function hasPayload() {
    return true; // 六场景的输入都在表单里，不依赖上游的「输入框必须非空」判断
  }

  function buildPayload(context) {
    const scenario = currentScenario();
    if (scenario.key === "kickoff") return { text: String(state.formValues.text || context.text || "").trim() };
    return { op: scenarioOp(scenario), params: opParams(scenario), input: buildInput() };
  }

  function modeBadge() {
    if (state.online) {
      return {
        text: "后端已连接 · 待运行",
        pill: "后端已连接",
        note: "<strong>后端已连接</strong> · 运行后打真实端点（失败自动回退离线演示）",
        connected: true,
      };
    }
    return {
      text: "离线演示（无后端）",
      pill: "离线演示",
      note: "<strong>离线演示（无后端）</strong> · 预置 mock，不是真实结果",
      connected: false,
    };
  }

  function onReview(record) {
    // 契约里没有「复核写后端」的端点，因此这里**不伪造**一个 POST：只把拍板写进本机，
    // 并在复核区留一句可核对的话。
    const saved = $("reviewSaved");
    if (saved) {
      saved.textContent = "已记录本机（" + (record.decision || "未选") + " · runId " + (record.run_id || "—") +
        "）；CONTRACT 未定义复核入库端点，故不上传";
    }
  }

  /* ------------------------------------------------------------------ 场景切换 */

  function syncScenario() {
    const key = (window.EduKit && EduKit.state && EduKit.state.scenarioKey) || state.scenarioKey;
    if (!key) return;
    const changed = key !== state.scenarioKey;
    state.scenarioKey = key;
    const scenario = scenarioByKey(key);
    if (!scenario) return;

    if (changed) {
      state.formValues = defaultFormValues(scenario);
      state.inputArtifact = "";
      state.inputMode = (state.snapshot && Array.isArray(state.snapshot.artifacts) &&
        state.snapshot.artifacts.some((item) => /\.(cif|vasp|poscar|xyz|extxyz|pdb)$/i.test(item.name || ""))) ? "run" : "cif_text";
      const box = $("cifResult");
      if (box) { box.hidden = true; box.innerHTML = ""; }
    }
    renderForm(scenario);
    renderInputSource(scenario);
    if (scenario.needsInput && !state.snapshot && state.currentRun) loadSnapshot(state.currentRun, { quiet: true });
    if (key === "library") refreshSources();
    syncAdvancedJson();
    refreshIcons();
  }

  function syncAdvancedJson() {
    const input = $("input");
    if (!input) return;
    const scenario = currentScenario();
    const payload = scenario.key === "kickoff"
      ? { text: state.formValues.text || "" }
      : { op: scenarioOp(scenario), params: opParams(scenario), input: buildInput() };
    try { input.value = JSON.stringify(payload, null, 2); } catch (error) { /* ignore */ }
    try { input.dispatchEvent(new Event("input", { bubbles: true })); } catch (error) { /* charCount 不关键 */ }
  }

  function syncFormFromJson() {
    const input = $("input");
    if (!input || state.syncing) return;
    const raw = String(input.value || "").trim();
    if (!raw) return;
    let parsed = null;
    try { parsed = JSON.parse(raw); } catch (error) { return; }
    if (!parsed || typeof parsed !== "object") return;
    state.syncing = true;
    try {
      const scenario = currentScenario();
      if (scenario.key === "kickoff" && typeof parsed.text === "string") {
        state.formValues.text = parsed.text;
      } else if (parsed.params && typeof parsed.params === "object") {
        Object.keys(parsed.params).forEach((key) => { state.formValues[key] = parsed.params[key]; });
        if (parsed.params.op) state.formValues.op = parsed.params.op;
      }
      if (parsed.input && typeof parsed.input === "object") {
        if (parsed.input.cif_text) { state.inputMode = "cif_text"; state.inputCif = String(parsed.input.cif_text); }
        else if (parsed.input.path) { state.inputMode = "path"; state.inputPath = String(parsed.input.path); }
        else if (parsed.input.run) { state.inputMode = "run"; state.currentRun = String(parsed.input.run); if (parsed.input.name) state.inputArtifact = String(parsed.input.name); }
      }
      renderForm(currentScenario());
      renderInputSource(currentScenario());
    } finally {
      state.syncing = false;
    }
  }

  /* ------------------------------------------------------------------ 事件 */

  function bindEvents() {
    const formBox = $("cifFormFields");
    if (formBox) {
      const onChange = () => {
        collectForm();
        const scenario = currentScenario();
        if (scenario.key === "inspect") renderForm(scenario); // op 切换 → 显示/隐藏 when 字段
        syncAdvancedJson();
      };
      formBox.addEventListener("input", onChange);
      formBox.addEventListener("change", onChange);
    }

    const inputBox = $("cifInputSource");
    if (inputBox) {
      inputBox.addEventListener("click", (event) => {
        const radio = event.target.closest('input[name="cifInputMode"]');
        if (!radio) return;
        state.inputMode = radio.value;
        renderInputSource(currentScenario());
        syncAdvancedJson();
      });
      inputBox.addEventListener("change", (event) => {
        if (event.target.id === "cifInputArtifact") { state.inputArtifact = event.target.value; syncAdvancedJson(); }
      });
      inputBox.addEventListener("input", (event) => {
        if (event.target.id === "cifInputCif") { state.inputCif = event.target.value; syncAdvancedJson(); }
        if (event.target.id === "cifInputPath") { state.inputPath = event.target.value; syncAdvancedJson(); }
      });
    }

    const resultBox = $("cifResult");
    if (resultBox) {
      resultBox.addEventListener("click", (event) => {
        const goto = event.target.closest("[data-cif-goto]");
        if (goto) { goToScenario(goto.dataset.cifGoto); return; }
        if (event.target.closest("#cifStartJob")) { startPipelineJob(); }
      });
    }

    const jobBlock = $("cifJobBlock");
    if (jobBlock) {
      jobBlock.addEventListener("click", (event) => {
        if (event.target.closest("#cifCancelJob")) cancelJob();
      });
    }

    const select = $("cifRunSelect");
    if (select) {
      select.addEventListener("change", async () => {
        state.currentRun = select.value || null;
        state.inputArtifact = "";
        await loadSnapshot(state.currentRun, { quiet: false });
        syncAdvancedJson();
      });
    }
    const reload = $("cifReloadRuns");
    if (reload) reload.addEventListener("click", () => refreshRuns());
    const newRun = $("cifNewRun");
    if (newRun) {
      newRun.addEventListener("click", () => {
        goToScenario("kickoff");
        toast("run 由执行动作创建：每跑一次管线/op 就新建一个 run 目录（不覆盖同名目录，CONTRACT §5.1）");
      });
    }
    const reloadSources = $("cifReloadSources");
    if (reloadSources) reloadSources.addEventListener("click", refreshSources);
    const adaptLink = $("cifAdaptLink");
    if (adaptLink) {
      adaptLink.addEventListener("click", (event) => {
        event.preventDefault();
        const dialog = $("helpDialog");
        if (dialog && typeof dialog.showModal === "function") dialog.showModal();
        else if (dialog) dialog.setAttribute("open", "");
        refreshIcons();
      });
    }

    const input = $("input");
    if (input) input.addEventListener("input", syncFormFromJson);

    ["sampleBtn", "clearBtn"].forEach((id) => {
      const button = $(id);
      if (button) button.addEventListener("click", () => setTimeout(() => {
        const scenario = currentScenario();
        state.formValues = defaultFormValues(scenario);
        renderForm(scenario);
        syncAdvancedJson();
      }, 0));
    });
  }

  function goToScenario(key) {
    const list = $("scenarioList");
    if (!list) return;
    const button = list.querySelector('[data-key="' + key + '"]');
    if (button) button.click();
  }

  function watchScenario() {
    const meta = $("scenarioMeta");
    if (!meta) return;
    new MutationObserver(() => syncScenario()).observe(meta, { childList: true, characterData: true, subtree: true });
  }

  /* ------------------------------------------------------------------ 启动 */

  async function boot() {
    installAdapter();
    bindEvents();
    setOnline(false);
    state.scenarioKey = (window.EduKit && EduKit.state && EduKit.state.scenarioKey) || state.scenarioKey;
    syncScenario();
    watchScenario();
    renderJobLog();
    renderSources();

    try {
      const data = await getJson(ENDPOINTS.bootstrap);
      state.boot = data;
      state.formats = (data.formats && typeof data.formats === "object" && Object.keys(data.formats).length)
        ? data.formats : ((window.CIF_REGISTRY && window.CIF_REGISTRY.formats) || {});
      state.bundles = (data.bundles && typeof data.bundles === "object" && Object.keys(data.bundles).length)
        ? data.bundles : ((window.CIF_REGISTRY && window.CIF_REGISTRY.bundles) || {});
      setOnline(true);
      patchScenarios(data.scenarios);
      renderTopbar();
      state.formValues = defaultFormValues(currentScenario());
      renderForm(currentScenario());
      renderInputSource(currentScenario());
      await refreshRuns({ quiet: true });
      await refreshSources();
      syncAdvancedJson();
    } catch (error) {
      setOnline(false);
      state.formats = (window.CIF_REGISTRY && window.CIF_REGISTRY.formats) || {};
      state.bundles = (window.CIF_REGISTRY && window.CIF_REGISTRY.bundles) || {};
      const info = errorLine(error);
      const hint = $("cifFormHint");
      if (hint) hint.textContent = "后端不可达（" + info.code + " · " + info.message + "）：表单照常可用，执行会走离线演示，不会白屏。";
      renderTopbar();
      state.formValues = defaultFormValues(currentScenario());
      renderForm(currentScenario());
      renderInputSource(currentScenario());
      syncAdvancedJson();
      console.warn("[cif] bootstrap 失败，已降级为离线演示：", info.code, info.message, info.hint);
    }

    refreshIcons();
    console.info("[cif] workbench layer ready · 后端在线=" + state.online);
  }

  function patchScenarios(remote) {
    if (!Array.isArray(remote) || !window.EDU_DATA) return false;
    const local = window.EDU_DATA.scenarios || [];
    let drifted = false;
    remote.forEach((item) => {
      const target = local.find((scenario) => scenario.key === (item.key || item.id));
      if (!target || !Array.isArray(item.stages)) return;
      // CONTRACT §3.3：Bootstrap.scenarios[].stages 的形状是 [{index,label,description}]，
      // 而 app.js 的 renderStages() 读的是字符串数组；这里统一归一到 label（两种形状都吃）。
      const labels = item.stages
        .map((stage) => (typeof stage === "string" ? stage : (stage && (stage.label || stage.name)) || ""))
        .filter((label) => label !== "");
      if (!labels.length) return;
      if (JSON.stringify(target.stages) !== JSON.stringify(labels)) {
        drifted = true;
        target.stages = labels;
      }
    });
    if (drifted) {
      state.drifted = true;
      console.warn("[cif] data.js 的 stages 与 /api/bootstrap 不一致，已以后端为准（请修 data.js）");
    }
    return drifted;
  }

  // 测试钩子：只读，供 _shots/render_probe.mjs 在**没有浏览器**的沙箱里验证「卡片 HTML 到底渲染成什么」。
  // 生产路径不读这些；它们只是把内部纯函数露出来，避免为了自测复制一份渲染逻辑。
  const _test = {
    resultCard: resultCard,
    productsHtml: productsHtml,
    validationHtml: validationHtml,
    summaryRows: summaryRows,
    fieldHtml: fieldHtml,
    opParams: opParams,
    scenarioOp: scenarioOp,
    defaultFormValues: defaultFormValues,
    buildInput: buildInput,
    scenarioByKey: scenarioByKey,
  };

  window.CIF = {
    hasPayload: hasPayload,
    buildPayload: buildPayload,
    modeBadge: modeBadge,
    onReview: onReview,
    state: state,
    _test: _test,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
