# 快速开始

语言：[English](../getting-started.md) | 简体中文

这个教程只做一件事：让你装好 `hermes-skill-guard`，确认本地状态正常，并保持 audit mode，直到你决定开启 enforcement。

## 1. 安装

```bash
pip install hermes-skill-guard
hermes plugins enable skill-guard
```

从源码开发：

```bash
uv sync --locked --extra dev
uv run hermes-skill-guard doctor
```

## 2. 检查存储和配置

```bash
hermes-skill-guard doctor
```

健康输出里应该能看到 `"ok": true` 和 `wal_enabled: true`。默认数据库位置：

```text
~/.hermes/skill-guard/state.db
```

默认模式不阻断：

```yaml
dry_run: true
enforcement:
  mode: audit
  fail_open: true
```

## 3. 在 Hermes 中使用

```bash
hermes chat
```

常用 slash commands：

```text
/skill-guard-doctor
/skill-guard-report
```

常用 tools：

- `skill_guard_preflight`
- `skill_guard_candidates`
- `skill_guard_report`
- `skill_guard_doctor`

## 4. Review 候选 skill

列出候选：

```bash
hermes-skill-guard candidates list
```

查看详情：

```bash
hermes-skill-guard candidates details <candidate_id>
```

批准或拒绝：

```bash
hermes-skill-guard candidates approve <candidate_id>
hermes-skill-guard candidates reject <candidate_id>
```

批准后创建 promotion attempt：

```bash
hermes-skill-guard candidates promote <candidate_id>
```

`promote` 不会偷偷把 skill 写进生产。它会创建一个可追踪的
`skill_manage create` attempt，并记录状态变化。

## 5. 之后再开启 enforcement

先观察。报告干净、operator 熟悉流程之后，再从 audit 切到 candidate：

```yaml
dry_run: false
enforcement:
  mode: candidate
  fail_open: true
```

只有在策略成熟且能接受误报时，才使用 `block`：

```yaml
dry_run: false
enforcement:
  mode: block
  fail_open: true
```

## 6. 自定义规则

创建 JSON 规则文件，然后设置 `HSG_RULES_PATH`：

```bash
export HSG_RULES_PATH="$PWD/docs/examples/custom-rules.json"
hermes-skill-guard rules validate --path "$HSG_RULES_PATH"
hermes-skill-guard rules list
```

完整 schema 和合并规则见英文 [Rule Engine](../rule-engine.md)。

## 7. 排查问题

先跑：

```bash
hermes-skill-guard doctor
hermes-skill-guard report --json
```

重点看：

- `sqlite_journal_mode` 是 `wal`。
- 首次上线时 `dry_run` 仍然打开。
- redaction counters 没有异常增长。
- `preflight_timeout_count` 为 0，或者超时有明确原因。
