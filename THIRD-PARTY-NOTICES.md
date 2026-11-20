# 第三方软件声明（THIRD-PARTY-NOTICES）

本仓库的浏览器前端（`web/`）随附以下第三方库的**本地副本**，运行时不访问任何 CDN，
页面加载与浏览路径零外网请求。所有第三方文件均**原样分发、未做任何修改**；
各库的许可证原文原样保留在 `web/vendor/licenses/`。版本与来源的机器可读对照另见
[web/SOURCES.md](web/SOURCES.md)。

| 库 | 版本 | 许可证 | 来源 | 本地路径 | 状态 |
| --- | --- | --- | --- | --- | --- |
| marked | 16.4.2 | MIT | <https://github.com/markedjs/marked>（npm `marked@16`，`lib/marked.umd.js`） | `web/vendor/marked.umd.js` | 使用中（Markdown 渲染） |
| DOMPurify | 3.4.17 | MPL-2.0 OR Apache-2.0 | <https://github.com/cure53/DOMPurify>（npm `dompurify@3`，`dist/purify.min.js`） | `web/vendor/dompurify.min.js` | 使用中（清洗 markdown / report） |
| KaTeX | 0.16.11 | MIT | <https://github.com/KaTeX/KaTeX>（npm `katex@0.16.11`） | `web/vendor/katex/`（含 60 个字体） | 使用中（公式） |
| Lucide | 0.468.0 | ISC | <https://github.com/lucide-icons/lucide>（npm `lucide@0.468.0`，`dist/umd/lucide.min.js`） | `web/vendor/lucide.min.js` | 使用中（本地生成 SVG 图标） |
| canvas-confetti | 1.9.3 | ISC | <https://github.com/catdad/canvas-confetti>（npm `canvas-confetti@1.9.3`） | `web/vendor/confetti.browser.min.js` | 使用中（拍板时的小动画） |
| Alpine.js | 3.x | MIT | <https://github.com/alpinejs/alpine>（npm `alpinejs@3`） | 未引入（仅保留许可证） | 备用 |
| Pico CSS | 2.1.1 | MIT | <https://github.com/picocss/pico>（npm `@picocss/pico@2`） | 未引入（仅保留许可证） | 备用 |
| Chart.js | 4.6.1 | MIT | <https://github.com/chartjs/Chart.js>（npm `chart.js@4`） | 未引入（仅保留许可证） | 备用 |
| Animate.css | 4.1.1 | MIT | <https://github.com/animate-css/animate.css>（npm `animate.css@4.1.1`） | 未引入（仅保留许可证） | 备用 |

许可证原文：`web/vendor/licenses/marked.LICENSE`、`dompurify.LICENSE`、`katex.LICENSE`、
`lucide.LICENSE`、`confetti.LICENSE`、`alpine.LICENSE`、`pico.LICENSE`、`chart.LICENSE`、
`animate.LICENSE`，共 9 份，全部原样保留。

## 使用纪律

- MIT / ISC / Apache-2.0：保留版权与许可证声明即可（本仓库随附上述 `licenses/` 原文）。
- DOMPurify 为 `MPL-2.0 OR Apache-2.0` 双许可；直接引用其 minified 发布文件不受 MPL 文件级义务影响。
- 页面输出的 Markdown（report.md、离线 mock、结构化摘要）一律先经 DOMPurify 清洗再渲染。
- 图标由 Lucide 的 `data-lucide` 属性在本地生成 SVG，不请求图标 CDN。
- 生成物 `web/assets/cif-registry.js` **不是**第三方文件：它由 `web/_shots/gen_registry.py` 从
  cifkit 自己的 `cifkit.export.FORMAT_INFO / BUNDLE_INFO` 实测导出（31 格式 / 12 输入包），
  随 cifkit 升级重跑该脚本即可。

## 运行时其它依赖

- 后端 `server.py` / `bridge.py`：仅 Python 标准库（不要额外 pip 包）。
- cifkit 本体及其依赖（pymatgen / ase / numpy 等）**不在本仓库分发**，请按
  [README.md](README.md) 的说明用 `CIFKIT_ROOT` 指向检出或 pip 安装。
