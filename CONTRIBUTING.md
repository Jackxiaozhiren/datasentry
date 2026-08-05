# 贡献指南

欢迎为 DataSentry 贡献代码。本项目按《产品设计与开发 Prompt 完整版 v2.0》
的开发流程演进（Step 阶段 + 3 连绿门禁），所有改动遵循下述约定。

## 开发环境

```bash
uv sync                 # 安装依赖（Python 3.12+，uv 项目工作区）
make check              # 门禁：lint + type + test（覆盖率 ≥ 85%）
make check-all          # 门禁 + M9 Demo + 性能基准
```

## 门禁（必须全绿方可提交）

| 目标 | 内容 |
| --- | --- |
| `make lint` | ruff check + ruff format --check |
| `make type` | mypy --strict（datasentry_core + datasentry，69+ 文件 0 错误） |
| `make test` | pytest 全套 + coverage 门禁（--cov-fail-under=85） |
| 3 连绿 | 提交前连续 3 次全量门禁通过（防 flaky） |

## 代码约定

- 类型标注：全量 mypy --strict（新代码必须通过严格类型检查）
- 风格：ruff 默认 + E/F/W/I/UP/B/SIM/RUF 规则集；100 列
- 全角标点豁免：中文报告/文案（RUF002/003 全局忽略；RUF001 按
  per-file-ignores 豁免 benchmarks、examples/demo、ui.py）
- 测试：pytest + coverage；新增功能必须带测试，覆盖新代码路径
- ADR：架构级决策（数据模型、接口契约、跨包改动）须在
  `docs/00-设计裁决记录-ADR.md` 追加 ADR 条目再实现

## 提交规范

- 单步单提交：一个 Step 的成果一次提交，消息含 Step 编号
  （如 `feat: REST API single-workspace facade (Step 23, ADR-023)`）
- 禁止提交机密：密钥、令牌、客户数据一律不进仓库
- 提交前检查：`git status` + `git diff` 确认只含本步文件

## 新增检测器 / 规则

1. 在 `packages/core/src/datasentry_core/detectors/` 实现（含规则元数据）
2. 加入 DETECTOR_REGISTRY 与 BenchRunner 预算表（ADR-007）
3. 测试：detector 单测 + 集成回归（tests/test_integration.py 同款）
4. README 检测器表格更新计数（当前 36）

## 性能与基准

- `make bench`（benchmarks/bench_scan.py）：1e6 行全量扫描 ≤ 60s 为验收档
- 引入新依赖（尤其是 C 扩展）须说明对扫描预算的影响

## 许可证

Apache-2.0。提交代码即表示同意以 Apache-2.0 许可发布您的贡献。
