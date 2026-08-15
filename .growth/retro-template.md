# 30 天增长复盘模板（第 30 天执行）

## 数据采集（发布后每天跑一次，30 天做汇总）

```bash
# star / fork / watch 曲线
gh api repos/Jackxiaozhiren/datasentry --jq '{stars: .stargazers_count, forks: .forks_count, watchers: .subscribers_count}'

# 流量（需 GitHub Analytics，网页端查看）
# https://github.com/Jackxiaozhiren/datasentry/graphs/traffic

# 每个渠道手动记录：帖子 URL、发布日期、互动数（points/转/赞/阅读）
```

## 复盘结构

### 1. 渠道漏斗（每个渠道一行）

| 渠道 | 内容 | 发布日 | 互动量 | 带来的 star | 转化率 | 经验 |
|------|------|--------|--------|------------|--------|------|
| Show HN | v0.24.0 帖 | | | | | |
| Dev.to | 博客 1 | | | | | |
| Dev.to | 博客 2 | | | | | |
| Reddit | 转贴 | | | | | |
| X | 主帖+回复链 | | | | | |
| LinkedIn | 长帖 | | | | | |
| 掘金 | 博客 1 中文 | | | | | |
| V2EX | 求反馈帖 | | | | | |
| 知乎 | 选型帖 | | | | | |
| OSCHINA | 项目发布 | | | | | |
| awesome PR | #12213 | | | | | |

### 2. 三问

- 哪个渠道的 star 转化率最高？（通常是 Show HN 或 Reddit）
- 哪个渠道互动最多但 star 转化最差？（互动 ≠ 转化，别被虚荣指标骗）
- 评论里被问得最多的 3 个问题是什么？（这是 README 缺失的信息，或下一篇博客的选题）

### 3. 诚实核对（不编数字）

- 如果 star 没涨：是内容问题还是时机问题？重发不是耻辱，但同一内容 3 个月内只发一次
- 如果某渠道完全无声：删掉或降级，30 天里止损也是收益
- awesome PR 状态：合并了？被拒了？（被拒原因本身就是信息）

### 4. 第 2 个 30 天决策

- 保留渠道清单（按上面漏斗排序取前 4）
- 新增渠道候选：HuggingFace 生态帖 / 播客 / 会议 lightning talk / GitHub Trending 冲刺（配合 Show HN 同日发布）
- 内容轮换：教程（新手向）→ 深度（架构向）→ 对比（选型向）三条线轮着写

### 5. 发布节奏检查

- 30 天内发布 ≥2 个版本（v0.24.0 → v0.25.0 ✓），每个版本都带 release notes
- README 首屏转化率是否够（3 行上手 + GIF + 场景代码）
- Discussions 是否有真实提问（没有就说明导流没到"使用"阶段）
