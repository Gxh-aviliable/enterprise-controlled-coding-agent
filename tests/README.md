# 测试目录结构

本目录包含 `enterprise_agent` 项目的单元测试和集成测试。

## 目录结构

```
tests/
├── __init__.py          # 测试包初始化
├── conftest.py          # pytest 配置和共享 fixtures
│
├── core/                # 核心 agent 模块测试
│   ├── __init__.py
│   ├── test_nodes.py    # LangGraph 节点测试
│   ├── test_state.py    # AgentState 定义测试
│   └── tools/           # 工具模块测试
│       ├── __init__.py
│       ├── test_file_ops.py    # 文件操作测试
│       ├── test_shell.py       # Shell 命令测试
│       ├── test_subagent.py    # 子 Agent 测试
│       ├── test_team.py        # Team 协作测试
│       ├── test_task.py        # 任务管理测试
│       ├── test_background.py  # 后台任务测试
│       ├── test_skills.py      # Skills 模块测试
│
├── memory/              # 记忆模块测试
│   ├── __init__.py
│   ├── test_memory.py   # 长期/短期记忆测试
│
└── api/                 # API 模块测试
    ├── __init__.py
```

## 运行测试

### 运行所有测试

```bash
cd my_mini_claude_code
pytest tests/
```

### 运行特定模块测试

```bash
# 工具测试
pytest tests/core/tools/

# 节点测试
pytest tests/core/test_nodes.py

# 记忆模块测试
pytest tests/memory/
```

### 运行单个测试文件

```bash
pytest tests/core/tools/test_file_ops.py
```

### 运行特定测试类

```bash
pytest tests/core/tools/test_file_ops.py::TestReadFile
```

### 运行特定测试方法

```bash
pytest tests/core/tools/test_file_ops.py::TestReadFile::test_read_existing_file
```

### 显示详细输出

```bash
pytest tests/ -v
```

### 显示覆盖率

```bash
pytest tests/ --cov=enterprise_agent --cov-report=html
```

## 测试分类

### 单元测试
- `test_file_ops.py`: 文件读写操作
- `test_shell.py`: Shell 命令执行和安全验证
- `test_task.py`: TodoWrite 和持久化任务管理
- `test_background.py`: 后台任务执行
- `test_skills.py`: Skills 加载和管理
- `test_nodes.py`: Agent 节点和路由逻辑
- `test_state.py`: AgentState 定义

### 异步测试
- `test_subagent.py`: 子 Agent 异步执行
- `test_team.py`: Team 协作异步消息传递

### 集成边界

- ChromaDB 长期记忆测试使用真实进程内 collection 和确定性离线 embedding，可在默认测试命令中运行。
- Redis/MySQL 路由和状态逻辑通过替身与隔离 API 测试覆盖；完整服务启动由 `scripts/docker_smoke_test.sh` 验证。
- 外部 LLM 任务成功率不属于 pytest 口径，统一由版本化 benchmark 报告记录。

## Fixtures

主要 fixtures 在 `conftest.py` 中定义:

| Fixture | 描述 |
|---------|------|
| `temp_workspace` | 临时工作目录 |
| `mock_workspace_env` | 设置 WORKSPACE_BASE 环境变量 |
| `mock_user_id` | 模拟用户 ID |
| `set_user_context` | 设置用户上下文变量 |
| `mock_llm_response` | 模拟 LLM 响应对象 |
| `sample_file_content` | 示例文件内容 |
| `sample_task_items` | 示例 TodoWrite 任务项 |

## 编写新测试

### 测试文件命名规范

- 文件名: `test_<module_name>.py`
- 类名: `Test<FeatureName>`
- 方法名: `test_<specific_behavior>`

### 测试模板

```python
"""Tests for <module_name> module."""

import pytest
from enterprise_agent.core.agent.tools.<module> import (
    <function_or_class>,
)


class Test<FeatureName>:
    """Test <feature> functionality."""

    @pytest.fixture
    def setup_data(self):
        """Create test data."""
        return {"key": "value"}

    def test_basic_behavior(self, setup_data):
        """Test basic behavior."""
        result = <function>(setup_data)
        assert result is not None

    @pytest.mark.asyncio
    async def test_async_behavior(self):
        """Test async behavior."""
        result = await <async_function>()
        assert result == expected
```

## 测试覆盖重点

1. **安全性**: Shell 命令黑名单、路径逃逸检测
2. **边界条件**: 空输入、不存在文件、无效参数
3. **错误处理**: 异常捕获、错误消息返回
4. **异步行为**: 并发消息传递、任务执行
5. **状态管理**: 任务状态转换、依赖关系
