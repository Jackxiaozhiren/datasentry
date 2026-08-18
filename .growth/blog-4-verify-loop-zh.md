# 验证修复：四种方式证明一次修复真的生效了

上一篇文章讲到修复闭环已经打通：`scan → compare → propose → apply → rollback`。
"打通"——但还没有"证明"。v0.46 → v0.52（之后七个版本）补上了每个质量闭环都需要、
但大多数工具都跳过的环节：**验证**。

修复写的是修复副本，从不覆盖源文件。这很安全，但它带来一个问题：*修复副本真的
修好了问题吗？有没有弄坏别的东西？* DataSentry 的做法不是"信任副本"，而是重新
扫描它，再与修复来源的那次扫描做对比。

## 关键一环：来源追踪

要做验证，必须知道"这次修复来自哪次扫描"。修复记录现在保存 `source_scan_run_id`
（schema v10），每一次已应用的修复都能指回它的来源扫描。这一个字段把快照变成了
闭环：

```
来源扫描 ──► 修复记录 ──► 修复副本
     ▲                          │
     └── 验证：重扫副本 ─────────┘
```

## 一键验证（Web）

每一处已应用的修复——修复历史页、批量 apply 结果页、工件页——都有 **Verify**
按钮：重扫修复副本，303 跳转到对比视图，以原始扫描为参考、修复后扫描为当前。

对比视图本来就会把问题按 NEW / FIXED / persistent 分组；v0.42 起每个 FIXED 组还会
显示关联的修复（`fixed by rep_…`）。现在这个链接直达修复的工件页，可以看到真实的
行级 diff：before 快照 vs 修复副本，变更单元格红/绿高亮。整条链路像故事一样可读：
*这个 run 坏了 → 这次修复改了这些行 → 重扫显示问题消失了。*

## CI 里的验证（CLI）

`datasentry repair verify <run_id>` 在终端里做同样的重扫：

```
$ datasentry repair verify rep_4bf447097915
fixed types: string_format
persistent types: categorical_anomaly, numeric_outlier
new types: (none)
```

退出码就是契约：`0` 表示修复没有引入回归（没有新增的 `new_types`）；`--require-clean`
更严格，要求**零残留**。两者都能直接当质量门禁：

```bash
datasentry repair apply-batch "$SCAN" --file orders.csv --all &&
  datasentry repair verify "$LAST_RUN" --require-clean
```

一个值得知道的细节：verify 门禁的默认标准是"无回归"而非"完全干净"。
`categorical_anomaly` 这类检测器几乎对每一列都会报警，如果要求重扫后完全干净，
门禁就形同虚设。"修复生效且没弄坏新东西"才是自动化应该守的线；"全部清零"是
产品决策，不是门禁。

## 给代理（MCP）和脚本（REST）的验证

代理通过 MCP 用 `repair_verify`——同样的报告，JSON，无需浏览器。脚本用
`POST /repairs/{id}/verify` 拿同样的负载，`GET /repairs/{id}/diff` 则只返回变更行
（`line` / `before` / `after`），不用传输整个文件。

## 处处可看 diff

工件页是 diff 的 Web 侧；`datasentry repair diff <run_id>` 在终端打印同样的变更
（`line 3: name: ' alice ' -> 'alice'`）；REST diff 端点喂给审计流水线。四个入口，
一套实现：Web UI、CLI、MCP server、REST API 全部调用同一个
`client.repair_verify` / `client.repair_diff`。

## 为什么这很重要

无法验证的修复只是猜测。上一轮闭环让修复可回滚；这一轮让它**可证明**：

- **有来源。** 每次修复都知道自己的来源扫描，"验证"是对比，不是承诺。
- **是门禁，不是感觉。** 退出码和 JSON 报告让 CI 能自动判断修复是否生效，
  并且自动发现回归。
- **带眼睛的审计。** 工件 diff 精确展示变更了哪些行、改前改后是什么——
  无论人还是代理，在任何入口都能看到。

闭环在本地打通，现在也在本地被证明——扫描、修复、证明、重复。