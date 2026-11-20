/* ============================================================
   cifkit 工作台 · 场景数据（assets/data.js）
   ------------------------------------------------------------
   上游：EduKit 复用包 assets/data.js（换题路径 #1：只改这个文件）。
   本文件把教育场景换成 cifkit 的「六场景窄闭环」，key 与顺序固定：
     kickoff / repair / geometry / export / inspect / library
   契约：CONTRACT.md §2 / §3.3 / §5

   stages 文案与后端 bootstrap.scenarios[].stages 一致（wb-backend 定稿）；
   cif.js 启动时仍会以 bootstrap 为准做一次比对（漂移即 console.warn 并以后端为准）。

   mock 是【离线演示】文本，不是 cifkit 的真实输出，也不含任何真实结构结论。
   String.raw 里不能出现裸反引号（会提前结束模板字面量），行内代码用 ${BT} 占位，
   写入时替换为一个反引号字符。

   cif 专属字段（供 assets/cif.js 渲染，未改上游 app.js 语义）：
     backend     这一屏背后的 CONTRACT 调用（只用于界面提示，不参与逻辑）
     form[]      表单字段（cif.js 按 kind 渲染；params 键名与 CONTRACT §5 一一对应）
     needsInput  true = 该 op 需要 input（三选一：已有 run 的产物 / cif_text / path）
     runLabel    「开始处理」按钮在本场景的文案
     next[]      结果卡片底部的「下一步入口」（切场景）
     mockResult  离线演示时结果卡片用的预置数字（卡片上必须标注「离线演示」）
   ============================================================ */

(function () {
  "use strict";

  const BT = String.fromCharCode(96);

  window.EDU_DATA = {
    project: {
      name: "cifkit 工作台",
      claim: "一句话开管线，结构由 cifkit 写，产物留在 run 目录",
      kicker: "CIFKIT · CRYSTAL STRUCTURE TOOLKIT",
    },

    scenarios: [
      /* ==================================================== 01 kickoff */
      {
        key: "kickoff",
        no: "01",
        icon: "sparkles",
        title: "一句话开管线",
        tagline: "一句话 → route 出 TaskSpec 与计划 → 确认后起 pipeline（SSE 实时日志）",
        short: "开工",
        backend: "POST /api/route →（确认）POST /api/job {kind: pipeline} → GET /api/job/events?id=（SSE）",
        inputLabel: "一句话请求（自由文本）",
        placeholder: "例如：从 core-mof 取一个 sod 拓扑的 Zn 结构，标准几何加工后导出 VASP 与计算输入包",
        accept: ".txt,.json",
        filesHint: "这一屏不读附件：结构由 cifkit 的数据源适配器按 spec 取（唯一会出网的路径之一）。",
        demoLine: "写一句话 → route 出 spec/plan → 确认起 pipeline → SSE 日志 + 步骤条 + 取消",
        stages: ["解析请求", "确认计划", "启动管线", "流式进度", "汇总产物"],
        outputs: ["TaskSpec", "计划表", "作业日志", "run 快照与 report.md"],
        runLabel: "路由预览",
        form: [
          {
            name: "text",
            kind: "textarea",
            label: "一句话要做什么",
            required: true,
            wide: true,
            rows: 4,
            placeholder: "从 core-mof 取一个 sod 拓扑的 Zn 结构，标准几何加工后导出 VASP 与计算输入包",
            help: "只走 POST /api/route，不执行；确认计划后再起 pipeline。",
          },
        ],
        next: [
          { key: "repair", label: "接着做结构修复" },
          { key: "export", label: "直接导出计算输入包" },
          { key: "library", label: "看已有 runs" },
        ],
        example: String.raw`{
  "text": "从 core-mof 取一个 sod 拓扑的 Zn 结构，标准几何加工后导出 VASP 与计算输入包"
}`,
        mock: String.raw`## 一句话开管线 · 离线演示

> **这是离线 mock 文本，不是 route / pipeline 的输出，也不含任何真实结构结论。**
> 后端不可达时页面显示这一屏；真实结果必须来自 cifkit（${BT}route${BT} + ${BT}pipeline${BT}）。

### 计划（plan，只展示不执行）

| # | 步骤 | 可选 | 说明 |
| --- | --- | --- | --- |
| 1 | resolve | 否 | 把一句话解析成 TaskSpec（源 / 拓扑 / 结构 id） |
| 2 | fetch | 否 | 按 spec 取结构（**唯一出网步骤**） |
| 3 | repair | 是 | 补氢 / 无序占位 / 过近原子 |
| 4 | geom | 否 | 标准化 / 超胞 / 切面 / 真空 |
| 5 | export | 否 | 渲染格式 + 计算输入包 |
| 6 | validate | 否 | 校验表写入 manifest.validation |
| 7 | report | 否 | report.md + manifest.json |

### 下一步（真实运行时）

1. 点「路由预览」→ ${BT}POST /api/route {"text":"…"}${BT}；
2. 看 spec/plan/warnings，确认后点「确认开工」→ ${BT}POST /api/job {"kind":"pipeline"}${BT}；
3. 页面用 ${BT}EventSource${BT} 订阅 ${BT}GET /api/job/events?id=${BT}：实时日志行 + 步骤条 + 取消按钮；
4. 作业 ${BT}done${BT} 后自动拉 ${BT}GET /api/run?id=<run_dir>${BT} 出结果卡片（结构数字 / 校验清单 / 产物 / 报告）。`,
        mockResult: {
          note: "离线演示 · 预置 mock，不是 cifkit 的真实输出",
          summary: [
            ["run 类型", "pipeline（离线演示）"],
            ["原子数 atoms", "2208"],
            ["组成 formula", "Zn96H960C768N384"],
            ["拓扑 topology", "sod"],
            ["步骤 n_steps", "10"],
            ["产物 n_artifacts", "12"],
          ],
          validation: {
            ok: true,
            checks: [
              { name: "natoms", ok: true, detail: "2208 个原子（离线演示占位）", severity: "error" },
              { name: "lattice", ok: true, detail: "晶格矩阵可解析（离线演示占位）", severity: "error" },
              { name: "formula", ok: true, detail: "Zn96H960C768N384（离线演示占位）", severity: "warn" },
            ],
          },
          products: [
            { name: "plan.json", bytes: 2048, kind: "json" },
            { name: "structure.vasp", bytes: 152719, kind: "text" },
            { name: "manifest.json", bytes: 8421, kind: "json" },
            { name: "report.md", bytes: 12044, kind: "text" },
            { name: "preview.png", bytes: 84210, kind: "image" },
          ],
          report: "### 离线演示报告\n\n这一屏没有真实 report.md：请在后端在线时运行，报告会从 run 目录的 report.md / manifest.json 读出。",
        },
        review: {
          type: "decision",
          label: "开工拍板",
          hint: "计划由人确认；改一句话重路由，或者只留计划不开工",
          options: ["确认开工（起 pipeline）", "改一句话重路由", "只看计划不开工"],
          notes: ["spec 与拓扑符合预期，起 pipeline", "源/拓扑选错了，改一句话", "先把 plan 拿给同事看"],
        },
      },

      /* ==================================================== 02 repair */
      {
        key: "repair",
        no: "02",
        icon: "wrench",
        title: "结构修复",
        tagline: "补氢 / 无序占位 / 过近原子检查，写 repaired.cif 与 op.json",
        short: "修复",
        backend: "POST /api/op {op: repair, params: {add_h, disorder, contacts}, input: {…}}",
        inputLabel: "修复请求（JSON，仅供参考）",
        placeholder: "例如：{\"op\":\"repair\",\"params\":{\"add_h\":true,\"disorder\":true,\"contacts\":false}}",
        accept: ".txt,.json,.cif",
        filesHint: "结构输入在「输入来源」里选：已有 run 的产物 / 粘贴 CIF / 服务端路径。",
        demoLine: "选输入 → 勾修复项 → 执行 op repair → 结果卡片（结构数字 / 校验 / 产物 / 报告）",
        stages: ["读入结构", "修复", "写出结构", "落 op.json"],
        outputs: ["repaired.cif", "op.json", "校验清单"],
        runLabel: "执行修复",
        needsInput: true,
        form: [
          { name: "add_h", kind: "checkbox", label: "补氢 add_h", default: true, help: "按价键与几何补氢（cifkit 自己的规则，不在前端计算）" },
          { name: "disorder", kind: "checkbox", label: "处理无序 disorder", default: true, help: "无序占位（partial occupancy）的处置" },
          { name: "contacts", kind: "checkbox", label: "近邻检查 contacts", default: false, help: "过近原子对报告（不静默删除原子）" },
        ],
        next: [
          { key: "geometry", label: "接着做几何加工" },
          { key: "inspect", label: "校验修复结果" },
          { key: "export", label: "导出计算输入包" },
        ],
        example: String.raw`{"op":"repair","params":{"add_h":true,"disorder":true,"contacts":false}}`,
        mock: String.raw`## 结构修复 · 离线演示

> **这是离线 mock 文本，不是 cifkit 的输出。** 真实结果取 ${BT}POST /api/op {op:"repair"}${BT}
> 的结构化返回与 run 目录里的 ${BT}repaired.cif${BT} / ${BT}op.json${BT}。

| 修复项 | 值（离线演示占位） |
| --- | --- |
| add_h | true |
| disorder | true |
| contacts | false |

- 修复只由 cifkit 执行：工作台不补氢、不改原子坐标、不删原子。
- 失败也会落 ${BT}op.json${BT}（${BT}ok:false${BT} + stderr 原文），便于复盘（CONTRACT §5.2）。`,
        mockResult: {
          note: "离线演示 · 预置 mock，不是 cifkit 的真实输出",
          summary: [
            ["原子数 natoms", "2210"],
            ["组成 formula", "Zn96H962C768N384（含补氢）"],
            ["晶胞 abc", "24.2 / 24.2 / 24.2 Å"],
            ["op", "repair"],
          ],
          validation: {
            ok: true,
            checks: [
              { name: "repaired.cif 可解析", ok: true, detail: "离线演示占位", severity: "error" },
              { name: "氢原子已补", ok: true, detail: "原文未给氢坐标，按价键补齐（离线演示占位）", severity: "warn" },
              { name: "近邻检查", ok: true, detail: "未发现 < 0.5 Å 的原子对（离线演示占位）", severity: "warn" },
            ],
          },
          products: [
            { name: "repaired.cif", bytes: 41208, kind: "structure" },
            { name: "op.json", bytes: 5120, kind: "json" },
            { name: "preview.png", bytes: 80122, kind: "image" },
          ],
          report: "### 离线演示报告\n\n真实 report.md 来自 run 目录；离线时不生成任何文件。",
        },
        review: {
          type: "decision",
          label: "修复拍板",
          hint: "补氢与无序处置由人确认；退回要写原因",
          options: ["采纳修复结构", "改修复项重跑", "退回不修复"],
          notes: ["补氢后价键合理", "先关掉 disorder 再跑一次", "原始结构已够用"],
        },
      },

      /* ==================================================== 03 geometry */
      {
        key: "geometry",
        no: "03",
        icon: "box",
        title: "几何加工",
        tagline: "标准化 / 超胞 / 切面 / 真空 / 簇，写 geom-<op>.cif 与 op.json",
        short: "几何",
        backend: "POST /api/op {op: geom, params: {standardize, supercell, surface, surface_frame, vacuum, thickness, cluster}}",
        inputLabel: "几何加工请求（JSON，仅供参考）",
        placeholder: '例如：{"op":"geom","params":{"standardize":true,"supercell":[2,2,2]}}',
        accept: ".txt,.json,.cif",
        filesHint: "结构输入在「输入来源」里选；加工一律由 cifkit 执行。",
        demoLine: "选输入 → 设加工参数 → 执行 op geom → 结果卡片（abc / 超胞 / 真空 / 产物）",
        stages: ["读入结构", "几何加工", "写出结构", "落 op.json"],
        outputs: ["geom-<op>.cif", "op.json", "校验清单"],
        runLabel: "执行加工",
        needsInput: true,
        form: [
          { name: "standardize", kind: "checkbox", label: "标准化 standardize", default: true, help: "按对称性取标准设置" },
          { name: "supercell", kind: "triple", label: "超胞 supercell [a,b,c]", defaults: [1, 1, 1], min: 1, step: 1, help: "留空或全 1 = 不变" },
          { name: "surface", kind: "triple", label: "切面 surface [h,k,l]", defaults: [0, 0, 1], step: 1 },
          { name: "surface_frame", kind: "select", label: "切面坐标系 surface_frame", default: "", help: "slab = 先切体相再面内重复（此时超胞 c 必须为 1，cifkit 规则）；cell = 先超胞再切该胞晶面",
            options: [
              { value: "", label: "（不指定 · 用 cifkit 默认）" },
              { value: "slab", label: "slab" },
              { value: "cell", label: "cell" },
            ] },
          { name: "vacuum", kind: "number", label: "真空层 vacuum (Å)", placeholder: "留空 = 不改", min: 0, step: 0.5 },
          { name: "thickness", kind: "number", label: "层厚 thickness (Å)", placeholder: "留空 = 不改", min: 0, step: 0.5 },
          { name: "cluster", kind: "select", label: "簇 cluster（从结构里取哪类簇）", default: "", help: "对应 cifkit geom --cluster；不做数值半径（cifkit 没有这个参数）",
            options: [
              { value: "", label: "（不切簇）" },
              { value: "sbu", label: "sbu" },
              { value: "metal_cluster", label: "metal_cluster" },
              { value: "core", label: "core" },
            ] },
        ],
        next: [
          { key: "export", label: "导出计算输入包" },
          { key: "inspect", label: "校验 / 特征 / 拓扑" },
          { key: "repair", label: "先做结构修复" },
        ],
        example: String.raw`{"op":"geom","params":{"standardize":true,"supercell":[1,1,1],"surface":[0,0,1]}}`,
        mock: String.raw`## 几何加工 · 离线演示

> **这是离线 mock 文本，不是 cifkit 的输出。** 真实结果取 ${BT}POST /api/op {op:"geom"}${BT}。

| 参数 | 值（离线演示占位） |
| --- | --- |
| standardize | true |
| supercell | [1,1,1] |
| surface | [0,0,1] |
| vacuum | 15.0 Å |

- 加工后晶胞与原子数由 cifkit 返回（${BT}result.natoms / result.formula / result.abc${BT}）；前端只展示，不计算。
- 真空层与切面参数只作为 cifkit 的入参；本页不推算任何几何量。`,
        mockResult: {
          note: "离线演示 · 预置 mock，不是 cifkit 的真实输出",
          summary: [
            ["原子数 natoms", "2208"],
            ["组成 formula", "Zn96H960C768N384"],
            ["晶胞 abc", "24.2 / 24.2 / 48.4 Å（2×2×2 占位）"],
            ["真空层 vacuum", "15.0 Å"],
          ],
          validation: {
            ok: true,
            checks: [
              { name: "geom 产物可解析", ok: true, detail: "离线演示占位", severity: "error" },
              { name: "晶胞体积 > 0", ok: true, detail: "离线演示占位", severity: "error" },
              { name: "真空层检查", ok: true, detail: "沿 c 轴 15.0 Å（离线演示占位）", severity: "warn" },
            ],
          },
          products: [
            { name: "geom-surface.cif", bytes: 42112, kind: "structure" },
            { name: "op.json", bytes: 5320, kind: "json" },
            { name: "preview.png", bytes: 81200, kind: "image" },
          ],
          report: "### 离线演示报告\n\n真实 report.md 来自 run 目录；离线时不生成任何文件。",
        },
        review: {
          type: "decision",
          label: "几何拍板",
          hint: "切面/超胞/真空由人确认；参数改错就退回重设",
          options: ["采纳加工结构", "改参数重跑", "退回结构修复"],
          notes: ["超胞与真空层符合计算设置", "切面指数写错了", "先把无序处理掉再切面"],
        },
      },

      /* ==================================================== 04 export */
      {
        key: "export",
        no: "04",
        icon: "package",
        title: "导出与计算输入包",
        tagline: "structure.* 多格式导出 + 12 组计算输入包（模板，不附 POTCAR/UPF）",
        short: "导出",
        backend: "POST /api/op {op: convert, params: {formats, bundles, stem, title}}",
        inputLabel: "导出请求（JSON，仅供参考）",
        placeholder: '例如：{"op":"convert","params":{"formats":["vasp","cif"],"bundles":["vasp"]}}',
        accept: ".txt,.json,.cif",
        filesHint: "格式与输入包清单来自 GET /api/bootstrap（cifkit 自己的注册表）；离线时用 assets/cif-registry.js（同源生成）。",
        demoLine: "选输入 → 勾格式与输入包 → 执行 op convert → 产物列表 + 下载 + Markdown 说明",
        stages: ["读入结构", "渲染格式", "生成输入包", "落 op.json"],
        outputs: ["structure.*", "计算输入包（模板）", "README / 缺件说明"],
        runLabel: "执行导出",
        needsInput: true,
        form: [
          { name: "formats", kind: "multiselect", label: "导出格式 formats", optionsFrom: "formats", defaults: ["vasp", "cif"], help: "来自 cifkit.export 注册表（31 个）" },
          { name: "bundles", kind: "multiselect", label: "计算输入包 bundles", optionsFrom: "bundles", defaults: [], help: "模板包：不附 POTCAR / UPF / 力场；缺件清单随包给出" },
          { name: "stem", kind: "text", label: "文件前缀 stem", placeholder: "structure" },
          { name: "title", kind: "text", label: "标题 title", placeholder: "留空 = 由结构推导" },
        ],
        next: [
          { key: "inspect", label: "校验导出结果" },
          { key: "library", label: "进数据集 / 溯源" },
          { key: "geometry", label: "回几何加工" },
        ],
        example: String.raw`{"op":"convert","params":{"formats":["vasp","cif"],"bundles":["vasp"]}}`,
        mock: String.raw`## 导出与计算输入包 · 离线演示

> **这是离线 mock 文本，不是 cifkit 的输出。** 真实结果取 ${BT}POST /api/op {op:"convert"}${BT}；
> 格式与输入包清单必须来自 cifkit 注册表（CONTRACT §3.1），本页离线时读同源的
> ${BT}assets/cif-registry.js${BT}（由 ${BT}cifkit.export${BT} 注册表实测导出）。

### 输入包口径（不许说成「可直接运行」）

- 输入包是**模板**：VASP 不附 POTCAR、QE 不附 UPF、CP2K 无 BASIS_SET、GROMACS 的 .top 类型/电荷是占位值。
- 每个包都带「缺件清单」；运行前必须自己补齐赝势、基组与截断收敛。
- 工作台不下载 POTCAR/UPF/力场（CONTRACT §8）。`,
        mockResult: {
          note: "离线演示 · 预置 mock，不是 cifkit 的真实输出",
          summary: [
            ["格式数 formats", "2（vasp, cif）"],
            ["输入包 bundles", "1（vasp）"],
            ["产物数 n_artifacts", "7"],
            ["op", "convert"],
          ],
          validation: {
            ok: true,
            checks: [
              { name: "structure.vasp", ok: true, detail: "VASP5 POSCAR，species 行已写（离线演示占位）", severity: "error" },
              { name: "structure.cif", ok: true, detail: "CifWriter 输出，symprec=None（离线演示占位）", severity: "error" },
              { name: "输入包缺件", ok: true, detail: "POTCAR 未附：需按你的 PAW 版本自选（离线演示占位）", severity: "warn" },
            ],
          },
          products: [
            { name: "structure.vasp", bytes: 152719, kind: "text" },
            { name: "structure.cif", bytes: 41208, kind: "structure" },
            { name: "INCAR", bytes: 1024, kind: "text" },
            { name: "KPOINTS", bytes: 512, kind: "text" },
            { name: "README.md", bytes: 4096, kind: "text" },
            { name: "preview.png", bytes: 84210, kind: "image" },
          ],
          report: "### 离线演示报告\n\n真实 report.md 来自 run 目录；离线时不生成任何文件。",
        },
        review: {
          type: "decision",
          label: "导出拍板",
          hint: "导出清单由人确认；输入包缺件必须写清楚",
          options: ["采纳导出产物", "改格式/输入包重跑", "退回几何加工"],
          notes: ["格式与目标软件匹配", "还要补一个 QE 输入包", "先改真空层再导出"],
        },
      },

      /* ==================================================== 05 inspect */
      {
        key: "inspect",
        no: "05",
        icon: "shield-check",
        title: "校验 / 特征 / 拓扑证据",
        tagline: "validate 校验表 · features 特征 CSV · topology 拓扑证据（缺省不比对）",
        short: "校验",
        backend: "POST /api/op {op: validate | features | topology}",
        inputLabel: "校验请求（JSON，仅供参考）",
        placeholder: '例如：{"op":"validate"} 或 {"op":"topology","params":{"net":"sod"}}',
        accept: ".txt,.json,.cif",
        filesHint: "输入在「输入来源」里选；三种 op 共用同一个输入。",
        demoLine: "选输入 → 选 op → 执行 → 校验清单 / 特征表 / 拓扑证据 + 产物下载",
        stages: ["读入结构", "校验", "特征与拓扑", "落 op.json"],
        outputs: ["validation.json / features.csv / topology.json", "校验清单"],
        runLabel: "执行校验",
        needsInput: true,
        form: [
          {
            name: "op",
            kind: "select",
            label: "操作 op",
            default: "validate",
            options: [
              { value: "validate", label: "validate · 校验表" },
              { value: "features", label: "features · 特征（RDF/环/拓扑）" },
              { value: "topology", label: "topology · 拓扑证据" },
            ],
          },
          { name: "rdf", kind: "checkbox", label: "features: rdf", default: true, when: "features" },
          { name: "rdf_bins", kind: "number", label: "features: rdf_bins", default: 100, min: 1, step: 1, when: "features" },
          { name: "rdf_max", kind: "number", label: "features: rdf_max (Å)", default: 10, min: 0.1, step: 0.5, when: "features" },
          { name: "topology", kind: "checkbox", label: "features: topology", default: true, when: "features" },
          { name: "rings", kind: "checkbox", label: "features: rings", default: false, when: "features" },
          {
            name: "net",
            kind: "select",
            label: "topology: net（留空 = 只报证据，不比对）",
            default: "",
            when: "topology",
            options: [
              { value: "", label: "（只报证据）" },
              { value: "sod", label: "sod" },
              { value: "pcu", label: "pcu" },
              { value: "dia", label: "dia" },
              { value: "fcu", label: "fcu" },
              { value: "tbo", label: "tbo" },
              { value: "bcu", label: "bcu" },
            ],
          },
        ],
        next: [
          { key: "export", label: "导出计算输入包" },
          { key: "library", label: "进数据集 / 溯源" },
          { key: "repair", label: "回结构修复" },
        ],
        example: String.raw`{"op":"validate","params":{}}`,
        mock: String.raw`## 校验 / 特征 / 拓扑证据 · 离线演示

> **这是离线 mock 文本，不是 cifkit 的输出。** 真实结果取 ${BT}POST /api/op${BT} 的结构化返回。

### validate（校验表）

| 项 | 结果（离线演示占位） |
| --- | --- |
| natoms | ✓ 2208 |
| lattice | ✓ 可解析 |
| occupancy | ✓ 无未处理的部分占据 |

### features / topology

- **拓扑只报证据**：${BT}net${BT} 缺省时不做比对，也不编造拓扑名（CONTRACT §5）。
- features 的 RDF / 环统计由 cifkit 计算；本页只展示 ${BT}features.csv${BT}。
- 每个 op 的失败也会落 ${BT}op.json${BT}（${BT}ok:false${BT} + stderr 原文）。`,
        mockResult: {
          note: "离线演示 · 预置 mock，不是 cifkit 的真实输出",
          summary: [
            ["op", "validate"],
            ["原子数 natoms", "2208"],
            ["校验项", "6 项（5 通过 / 1 警告）"],
            ["拓扑", "sod（离线演示占位）"],
          ],
          validation: {
            ok: true,
            checks: [
              { name: "natoms", ok: true, detail: "2208（离线演示占位）", severity: "error" },
              { name: "lattice", ok: true, detail: "晶格矩阵可解析（离线演示占位）", severity: "error" },
              { name: "occupancy", ok: true, detail: "无部分占据未处理（离线演示占位）", severity: "error" },
              { name: "coordination", ok: true, detail: "配位数分布偏宽（离线演示占位）", severity: "warn" },
            ],
          },
          products: [
            { name: "validation.json", bytes: 3200, kind: "json" },
            { name: "features.csv", bytes: 20480, kind: "text" },
            { name: "topology.json", bytes: 4096, kind: "json" },
            { name: "preview.png", bytes: 82400, kind: "image" },
          ],
          report: "### 离线演示报告\n\n真实 report.md 来自 run 目录；离线时不生成任何文件。",
        },
        review: {
          type: "decision",
          label: "校验拍板",
          hint: "证据链由人核对；不通过就退回重做，别硬判通过",
          options: ["采纳校验结论", "退回重做", "只保留证据不下结论"],
          notes: ["校验表与结构一致", "有 warning，先回修复", "证据留档即可"],
        },
      },

      /* ==================================================== 06 library */
      {
        key: "library",
        no: "06",
        icon: "database",
        title: "库与溯源 / 数据集",
        tagline: "浏览 runs 与数据源状态 → 构建 dataset.jsonl（可选特征/结构/dedupe）",
        short: "库",
        backend: "GET /api/runs + GET /api/sources + POST /api/op {op: dataset}",
        inputLabel: "数据集请求（JSON，仅供参考）",
        placeholder: '例如：{"op":"dataset","params":{"from_runs":true,"label_key":"topology"}}',
        accept: ".txt,.json",
        filesHint: "这一屏只读 runs 目录与数据源状态；构建数据集会新建一个 run 目录。",
        demoLine: "看 runs 与数据源 → 勾数据集选项 → 执行 op dataset → dataset.jsonl + 统计 + 溯源",
        stages: ["浏览 runs", "查看 run 快照", "构建数据集", "落 op.json"],
        outputs: ["dataset/dataset.jsonl", "manifest.json", "溯源清单"],
        runLabel: "构建数据集",
        form: [
          { name: "from_runs", kind: "checkbox", label: "from_runs（从 runs 目录收集）", default: true },
          { name: "from_library", kind: "checkbox", label: "from_library（从数据源库收集）", default: false, help: "可能触发联网取结构（显式动作）" },
          { name: "label_key", kind: "text", label: "label_key（标签列，如 topology）", placeholder: "留空 = 不设标签" },
          { name: "no_features", kind: "checkbox", label: "no_features（不算特征）", default: false },
          { name: "no_structures", kind: "checkbox", label: "no_structures（不复制结构文件）", default: false },
          { name: "dedupe", kind: "select", label: "dedupe（按哪个键去重）", default: "", help: "对应 cifkit dataset --dedupe；留空 = 不去重（cifkit 默认）",
            options: [
              { value: "", label: "（不去重）" },
              { value: "structure_sha256", label: "structure_sha256" },
              { value: "structure_file", label: "structure_file" },
              { value: "formula", label: "formula" },
              { value: "reduced_formula", label: "reduced_formula" },
              { value: "label", label: "label" },
            ] },
          { name: "jobs", kind: "number", label: "jobs（并行度）", default: 4, min: 1, max: 32, step: 1 },
        ],
        next: [
          { key: "inspect", label: "对某个 run 做校验" },
          { key: "kickoff", label: "再开一条管线" },
        ],
        example: String.raw`{"op":"dataset","params":{"from_runs":true,"label_key":"topology"}}`,
        mock: String.raw`## 库与溯源 / 数据集 · 离线演示

> **这是离线 mock 文本。** runs 列表在本页由 ${BT}GET /api/runs${BT} 实时填充；
> 离线时只显示这一屏 mock，**不假装读到了你的 runs 目录**。

### 数据集口径

- ${BT}label_key${BT} 是**列名**不是猜测：没有这一列就不设标签（不编造标签）。
- ${BT}from_library${BT} 可能触发联网取结构 —— 属于唯一会出网的显式动作（CONTRACT §0/§5）。
- 去重按结构指纹；不通过「相似」推断重复。
- 产物：${BT}dataset/dataset.jsonl${BT}、${BT}manifest.json${BT}、溯源清单。`,
        mockResult: {
          note: "离线演示 · 预置 mock，不是 cifkit 的真实输出",
          summary: [
            ["run 数", "3（离线演示占位）"],
            ["数据源 ready", "1 / 4（离线演示占位）"],
            ["dataset 行数", "12"],
            ["去重后", "11"],
          ],
          validation: {
            ok: true,
            checks: [
              { name: "dataset.jsonl", ok: true, detail: "12 行可解析（离线演示占位）", severity: "error" },
              { name: "manifest.json", ok: true, detail: "含溯源字段（离线演示占位）", severity: "error" },
              { name: "label_key", ok: true, detail: "未指定，不设标签（离线演示占位）", severity: "warn" },
            ],
          },
          products: [
            { name: "dataset/dataset.jsonl", bytes: 61440, kind: "json" },
            { name: "dataset/manifest.json", bytes: 8192, kind: "json" },
            { name: "dataset/README.md", bytes: 2048, kind: "text" },
          ],
          report: "### 离线演示报告\n\n真实 runs 列表与 dataset 统计需要后端在线（GET /api/runs / POST /api/op）。",
        },
        review: {
          type: "decision",
          label: "数据集拍板",
          hint: "收集范围与去重口径由人确认；用到外部库要写清来源",
          options: ["采纳本次数据集", "改范围重建", "只保留 runs 溯源"],
          notes: ["只收 runs，不碰外部库", "label_key 写错了", "先去重再建"],
        },
      },
    ],
  };
})();
