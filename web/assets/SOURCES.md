# 第三方前端资产（cifkit 工作台）

全部沿用上游 EduKit 复用包 `vendor/` 的本地副本，**运行时零 CDN 请求**，断网可演示。
本目录第三方文件的版本、来源与许可证与上游一致（见下表），许可证原文在 `vendor/licenses/`。

| 资产 | 版本 | 许可证 | 本地路径 | 状态 | 来源 |
| --- | --- | --- | --- | --- | --- |
| marked | 16.4.2 | MIT | `vendor/marked.umd.js` | 使用中（Markdown 渲染） | https://cdn.jsdelivr.net/npm/marked@16/lib/marked.umd.js |
| DOMPurify | 3.4.17 | MPL-2.0 OR Apache-2.0 | `vendor/dompurify.min.js` | 使用中（清洗 markdown / report） | https://cdn.jsdelivr.net/npm/dompurify@3/dist/purify.min.js |
| KaTeX | 0.16.11 | MIT | `vendor/katex/` | 使用中（公式，含 60 个字体） | https://registry.npmjs.org/katex/-/katex-0.16.11.tgz |
| Lucide | 0.468.0 | ISC | `vendor/lucide.min.js` | 使用中（本地生成 SVG 图标） | https://cdn.jsdelivr.net/npm/lucide@0.468.0/dist/umd/lucide.min.js |
| canvas-confetti | 1.9.3 | ISC | `vendor/confetti.browser.min.js` | 使用中（拍板时的小动画） | https://cdn.jsdelivr.net/npm/canvas-confetti@1.9.3/dist/confetti.browser.min.js |
| Alpine.js | 3.x | MIT | 未引入 | 备用 | https://cdn.jsdelivr.net/npm/alpinejs@3/dist/cdn.min.js |
| Pico CSS | 2.1.1 | MIT | 未引入 | 备用 | https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css |
| Chart.js | 4.6.1 | MIT | 未引入 | 备用 | https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js |
| Animate.css | 4.1.1 | MIT | 未引入 | 备用 | https://cdn.jsdelivr.net/npm/animate.css@4.1.1/animate.min.css |

许可证原文（原样保留）：
`vendor/licenses/marked.LICENSE`、`dompurify.LICENSE`、`katex.LICENSE`、`lucide.LICENSE`、
`confetti.LICENSE`、`alpine.LICENSE`、`pico.LICENSE`、`chart.LICENSE`、`animate.LICENSE`。

## 生成物（不是第三方，但也在 assets 里）

| 文件 | 来源 | 说明 |
| --- | --- | --- |
| `assets/cif-registry.js` | **本机 cifkit 仓库自身**：`cifkit.export.FORMAT_INFO` / `BUNDLE_INFO` | 由 `_shots/gen_registry.py` 实测导出（31 格式 / 12 输入包），作为 `GET /api/bootstrap` 不可达时的离线兜底。CONTRACT 3.1 要求格式清单「必须来自 cifkit 自己」，所以这里不手写；升级 cifkit 后重跑该脚本即可。 |

## 使用纪律

- MIT / ISC / Apache-2.0：保留版权与许可证声明即可。
- DOMPurify 是 MPL-2.0 OR Apache-2.0 双许可；直接引用 minified 文件不受 MPL 文件级义务影响。
- 所有页面输出的 Markdown（report.md、离线 mock、结构化摘要）都先经 DOMPurify 清洗再渲染。
- 图标通过 Lucide 的 data-lucide 属性在本地生成 SVG，不请求图标 CDN。

## 唯一会出网的路径（与第三方无关，写在这里便于对照）

页面加载、切换场景、看 run 快照、下载产物**都不出网**。唯一出网的是用户显式动作：
`op=fetch`、`op=sources_probe`、`kind=pipeline`（由后端进程内/子进程调 cifkit 的数据源适配器联网）。
本前端的六场景窄闭环不包含前两者；pipeline 需要用户在 route 预览后显式点「确认开工」。
