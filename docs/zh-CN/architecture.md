# 架构说明

语言：[English](../architecture.md) | 简体中文

`hermes-skill-guard` 是 Hermes 插件，不是 Hermes core patch。它通过
Hermes v0.14 插件 API 注册 tools、hooks、slash commands、CLI command 和一个只读 bundled skill。

## 组件图

```mermaid
flowchart TD
    Hermes[Hermes Agent v0.14+] --> Register[plugin.register]
    Register --> Probe[CapabilityProbe]
    Probe --> Adapter[HermesAdapter]
    Register --> Adapter
    Adapter --> Tools[Plugin Tools]
    Adapter --> Hooks[Lifecycle Hooks]
    Adapter --> Slash[Slash Commands]
    Adapter --> CLI[Hermes CLI Command]
    Adapter --> Skill[Bundled Skill]
    Hooks --> Pre[pre_tool_call]
    Hooks --> Post[post_tool_call]
    Pre --> Policy[PreflightPolicy]
    Policy --> Cache[TraceCache]
    Post --> Cache
    Post --> Redactor[Redactor]
    Redactor --> Store[(SQLite WAL)]
    Tools --> Store
    CLI --> Store
    Slash --> Store
    Probe --> Store
```

## 一次 skill 创建的路径

```mermaid
sequenceDiagram
    participant User as User / Agent
    participant Hermes as Hermes Agent
    participant Guard as skill-guard
    participant Policy as PreflightPolicy
    participant Store as SQLite WAL

    User->>Hermes: skill_manage create
    Hermes->>Guard: pre_tool_call(tool_name,args,tool_call_id)
    Guard->>Policy: evaluate ToolCall
    Policy-->>Guard: allow / warn / candidate / block
    Guard->>Guard: cache decision by tool_call_id
    alt audit or dry_run
        Guard-->>Hermes: None
        Hermes->>Hermes: execute tool
    else candidate or block
        Guard-->>Hermes: {"action":"block","message":"..."}
    end
    Hermes->>Guard: post_tool_call(result,duration,tool_call_id)
    Guard->>Guard: redact payload
    Guard->>Store: write event, audit row, candidate
```

## 候选状态

```mermaid
stateDiagram-v2
    [*] --> detected
    detected --> candidate: stage
    candidate --> approved: approve
    candidate --> rejected: reject
    approved --> promoted: promotion observed
    approved --> archived: archive
    rejected --> archived: archive
    promoted --> archived: archive
```

## 主要模块

| 模块 | 责任 |
|---|---|
| `HermesAdapter` | 包一层 Hermes `PluginContext`，优先使用 v0.14 keyword signatures。 |
| `PreflightIntent` | 注册 `skill_guard_preflight` 和 `pre_tool_call`。 |
| `CaptureIntent` | 注册 `post_tool_call`，持久化脱敏事件。 |
| `CompatibilityIntent` | 探测 Hermes capability matrix，退休已被官方能力覆盖的 intent。 |
| `CandidatesIntent` | 候选列表、批准、拒绝和详情。 |
| `PromotionIntent` | promotion attempt 和状态机收敛。 |
| `RelationsIntent` | 标记 duplicate、conflict、supersedes、depends_on、related_to。 |
| `ReportingIntent` | `report`、`doctor`、slash commands 和 Hermes CLI bridge。 |
| `AutoPromoteIntent` | 扫描已批准候选，并在时间和关系 gate 通过后创建 promotion attempt。 |
| `StateStore` | SQLite WAL 存储、迁移、counter、候选状态和 module status。 |

## 边界

插件不做这些事：

- 不监听整个 skills 文件夹。
- 不替代 Hermes curator。
- 不扫描所有已有 skill。
- 不默认自动 promotion。
- 不把原始 payload 写进数据库，除非 operator 显式打开。

这些边界让插件保持可解释，也让上线时的失败模式更简单。
