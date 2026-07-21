# 从 macOS 开发环境部署到远程 Linux 服务器

本文档面向 Mini Claude Code 当前的单机 Docker Compose 交付形态，说明如何把在 macOS 上开发的代码部署到远程 Linux 服务器，以及后续如何更新、备份和回滚。

当前仓库已经包含 API、Vue/Nginx、MySQL、Redis Stack、Chroma 持久目录和 workspace 持久卷。本文档适合校招作品集演示、个人服务器和小规模内网试用；直接暴露到公网或用于企业生产前，还需要完成文末列出的安全加固。

## 1. 先理解：部署不只是“上传代码”

部署时应将源码、服务器配置和运行数据分开管理。

| 内容 | 推荐处理方式 | 是否随版本更新 |
|---|---|---|
| Python、Vue、Docker 等源码 | 推送到 GitHub/GitLab，服务器 clone 或 pull | 是 |
| Docker 镜像 | 在 Linux 服务器构建，或由 CI 构建后推送镜像仓库 | 是 |
| .env、模型 API Key、JWT 密钥 | 仅在服务器创建，禁止提交 Git | 否 |
| Python .venv、node_modules | 不从 Mac 上传，在 Linux/Docker 内重新安装 | 否 |
| MySQL、Redis、workspace、Chroma | 保存在服务器 Docker volume | 否 |
| Hugging Face 模型缓存 | 首次启动下载到服务器持久卷 | 否 |
| 日志和备份 | 保存在服务器或独立存储 | 否 |

推荐流程：

    macOS 开发
        ↓ git commit / push
    GitHub 或 GitLab
        ↓ clone / fetch / checkout tag
    Linux 服务器
        ↓ docker compose build
    API + Vue/Nginx + MySQL + Redis
        ↓
    Docker volumes 保存 workspace、Chroma、数据库和模型缓存

第一次需要把源码 clone 到服务器。后续不需要反复压缩和手工上传，只需拉取新版本并重建发生变化的容器。

## 2. 三种代码交付方式

### 2.1 Git 拉取源码：当前最推荐

适用于作品集演示和单台服务器。

优点：

- 更新和回滚清楚；
- 能准确记录服务器运行的是哪个 commit 或 tag；
- 不会误上传 .venv、node_modules 和本地数据；
- 与当前 Compose 的源码构建方式完全兼容。

流程：

    Mac push → 服务器 git fetch → checkout tag → docker compose up -d --build

### 2.2 CI 构建镜像：后续更专业

适用于稳定发布和多台服务器。

    Mac push
        ↓
    GitHub Actions / GitLab CI
        ↓ 构建 linux/amd64 或多架构镜像
    GHCR / Docker Hub / 企业镜像仓库
        ↓
    服务器 docker compose pull && docker compose up -d

这种方式下，服务器可以只保存 Compose 文件和环境变量，不必保存完整源码。它也是后续实现自动化发布、镜像扫描和版本回滚的推荐方向。

### 2.3 rsync/scp：仅适合临时测试

没有 Git 远端时可以临时同步，但不要复制以下目录和文件：

- .git
- .env
- .venv
- node_modules
- workspaces
- chroma_data
- benchmark 临时结果

手工同步容易造成文件遗漏、服务器版本不可追踪和误覆盖运行数据，不建议作为长期发布流程。

## 3. Mac 与 Linux 的关键差异

### 3.1 不要复制 Mac 的虚拟环境

Python wheel、二进制扩展和解释器路径与操作系统相关。服务器应在 Linux 容器内按照 uv.lock 重新安装。

### 3.2 Apple Silicon 与常见服务器架构不同

M 系列 Mac 通常是 arm64，云服务器通常是 linux/amd64。不要默认把 Mac 本地构建的镜像导出后直接放到服务器。

当前最简单可靠的方案是在目标 Linux 服务器执行 Docker build。使用 CI 时，则显式构建 linux/amd64 或多架构镜像。

### 3.3 路径和文件名大小写

- Mac 开发路径可能包含中文、空格或用户目录；
- Linux 容器内统一使用 /workspaces、/data/chroma 和 /data/huggingface；
- Linux 文件名大小写敏感；
- 不要把 Mac 的绝对路径写进服务器 .env。

## 4. 服务器准备

### 4.1 建议配置

用于单人作品集演示时，建议：

- Ubuntu 22.04 或 24.04；
- 2 至 4 核 CPU；
- 8 GB 内存；
- 30 至 50 GB 可用磁盘；
- 能访问模型 API、Python 包源、Docker Registry 和首次 embedding 模型下载地址。

当前 API 使用 CPU-only PyTorch，不要求 GPU。内存过小时，首次加载 sentence-transformers 或同时运行构建、MySQL 和 Redis 可能出现资源压力。

### 4.2 基础准备

服务器需要：

- 普通部署用户和 SSH Key 登录；
- Docker Engine；
- Docker Compose Plugin；
- Git；
- 域名和 DNS 解析（需要公网 HTTPS 时）；
- 仅开放必要的防火墙端口。

推荐只对公网开放：

| 端口 | 用途 |
|---:|---|
| 22 | SSH，最好限制来源 IP |
| 80 | HTTP，用于跳转 HTTPS 或证书签发 |
| 443 | HTTPS |

不要对公网开放 MySQL 3306、Redis 6379、FastAPI 8000。

## 5. Mac 端发布准备

服务器只能获取已经提交并推送的内容。当前本地未提交的修改不会自动出现在远端。

发布前执行：

    git status
    git diff --check
    .venv/bin/python -m pytest -q
    .venv/bin/ruff check enterprise_agent tests benchmarks scripts
    npm run build --prefix frontend
    docker compose -f docker/docker-compose.yml config -q

确认修改范围后提交：

    git add <确认过的文件>
    git commit -m "feat: prepare portfolio server deployment"
    git push origin feature/portfolio-hardening

准备正式演示版本时，建议合并到稳定分支并打 tag：

    git tag v0.1.0
    git push origin v0.1.0

服务器应优先 checkout tag，而不是长期跟随一个不断变化的开发分支。

## 6. 服务器首次部署

以下示例假设：

- 部署用户为 deploy；
- 项目目录为 /opt/mini-claude-code；
- 对外域名为 agent.example.com；
- Git 远端地址需要替换为实际仓库。

### 6.1 克隆固定版本

    ssh deploy@<server-ip>
    sudo mkdir -p /opt/mini-claude-code
    sudo chown deploy:deploy /opt/mini-claude-code

    git clone <repository-url> /opt/mini-claude-code
    cd /opt/mini-claude-code
    git checkout v0.1.0

私有仓库建议给服务器配置只读 Deploy Key，不要把个人 GitHub 密码或长期访问令牌写进脚本。

### 6.2 创建服务器专用环境变量

    cp .env.example .env
    chmod 600 .env

至少检查和修改：

    DEBUG=false

    JWT_SECRET_KEY=<至少32字符的随机密钥>

    LLM_PROVIDER=<实际提供商>
    LLM_API_KEY=<真实密钥>
    LLM_BASE_URL=<需要时填写>
    MODEL_ID=<实际模型>

    CORS_ORIGINS=https://agent.example.com

    WORKSPACE_BASE=/workspaces
    FILE_OPEN_MODE=web-vscode
    VSCODE_WEB_BASE_URL=https://code.example.com

可以在服务器生成 JWT 密钥：

    openssl rand -hex 32

注意：

- .env 不能提交 Git；
- 不要在工单、截图、聊天记录和部署日志中打印 .env；
- 如果使用外部 LLM，Agent 读取的代码上下文可能发送给模型供应商；
- 严格内网场景应接入经过验证的内部兼容模型端点。

### 6.3 当前 Compose 上公网前必须处理的配置

当前 [docker-compose.yml](../docker/docker-compose.yml) 是已通过本地验收的演示基线，但仍有以下生产差距：

1. MySQL root 和应用密码仍是示例值并写在 Compose 中；
2. API 和前端端口默认绑定所有宿主机网卡；
3. 没有独立的生产 Compose 文件；
4. 没有密钥管理系统。

公网部署前应：

- 将 MySQL root、应用用户密码改为服务器环境变量或 secret；
- 将前端端口绑定到 127.0.0.1:3000，由宿主机反向代理访问；
- 将 API 绑定到 127.0.0.1:8000，或取消 API 的宿主机端口，仅允许前端容器通过 Docker 网络访问；
- 保持 MySQL、Redis 仅绑定回环地址；
- 不要继续使用 rootpassword 和 agent_password。

如果只是临时在受控内网演示，也至少应替换所有示例密码，并通过安全组限制来源 IP。

### 6.4 构建和启动

    cd /opt/mini-claude-code

    docker compose -f docker/docker-compose.yml config -q
    docker compose -f docker/docker-compose.yml up -d --build
    docker compose -f docker/docker-compose.yml ps

启动顺序由健康检查控制：

    MySQL + Redis
          ↓ healthy
        FastAPI
          ↓ healthy
      Vue / Nginx

第一次启动可能下载 embedding 模型，API health check 已预留较长 start period。下载完成后，后续进程会优先使用持久卷中的本地缓存，不再主动检查 Hugging Face。完全离线的服务器应先预热 `huggingface_cache`，再设置：

    EMBEDDING_ALLOW_DOWNLOAD=false

这样缓存缺失时 API 会给出明确错误，而不是尝试访问公网。网络较慢时应查看日志，而不是反复删除容器和卷：

    docker compose -f docker/docker-compose.yml logs --tail=200 api

### 6.5 验证

    curl http://127.0.0.1:3000/healthz
    curl http://127.0.0.1:3000/api/health
    docker compose -f docker/docker-compose.yml ps

预期：

- frontend、api、mysql、redis 均为 healthy；
- /healthz 返回 ok；
- /api/health 中 MySQL、Redis 均为 ok。

也可以先运行仓库提供的隔离 smoke test：

    ./scripts/docker_smoke_test.sh

该脚本使用独立 Compose project 和替代端口，不会占用默认部署端口；结束后默认清理测试容器，但保留下载缓存卷。

## 7. 配置域名和 HTTPS

不要直接让用户访问 http://服务器IP:3000。推荐在宿主机使用 Caddy 或 Nginx 终止 TLS：

    浏览器
        ↓ HTTPS :443
    Caddy / 宿主机 Nginx
        ↓ 127.0.0.1:3000
    Vue / 容器 Nginx
        ↓ Docker 网络中的 /api
    FastAPI
        ├── MySQL
        ├── Redis
        └── workspace / Chroma / HF volumes

Caddy 的最小思路：

    agent.example.com {
        reverse_proxy 127.0.0.1:3000
    }

完成 DNS 解析后，再由 Caddy 或 Nginx 配置 HTTPS 证书、HTTP 到 HTTPS 跳转、安全响应头和访问日志。

因为 SSE 任务可能持续较长时间，外层反向代理需要：

- 禁用对 SSE 响应的代理缓冲；
- 提高读取超时；
- 保留 X-Forwarded-For、X-Forwarded-Proto；
- 确保连接不会被 CDN 或负载均衡器过早关闭。

仓库内 [nginx.conf](../docker/nginx.conf) 已为前端到 FastAPI 的 /api 反向代理关闭缓冲并设置 600 秒超时；宿主机外层代理也需要匹配。

## 8. Web VSCode / code-server

当前 Compose 会设置 FILE_OPEN_MODE=web-vscode，但并没有自动部署 code-server。

如果需要用户在浏览器打开服务器 workspace，还要单独部署 code-server 或 OpenVSCode Server，并满足：

- 与 API 挂载同一个 workspace_data 到 /workspaces；
- 使用独立域名，例如 code.example.com；
- 配置登录认证和 HTTPS；
- 限制用户只能访问其授权 workspace；
- 正确设置 VSCODE_WEB_BASE_URL 或 VSCODE_WEB_URL_TEMPLATE。

仅设置 FILE_OPEN_MODE=web-vscode 并不会自动获得一个可用的 Web IDE。

如果作品集演示不需要 Web IDE，可以先演示内置文件树、文件查看和 Trace 回放，避免额外扩大攻击面。

## 9. 持久数据与备份

当前 Compose 使用以下命名卷：

| Volume | 内容 | 是否重要 |
|---|---|---|
| mysql_data | 用户、会话元数据 | 必须备份 |
| redis_data | LangGraph checkpoint、恢复状态 | 建议备份 |
| workspace_data | 用户代码和 Agent 修改结果 | 必须备份 |
| chroma_data | 长期语义记忆 | 建议备份 |
| huggingface_cache | embedding 模型缓存 | 可重新下载 |

更新代码时不要执行：

    docker compose down -v

其中 -v 会删除命名卷。普通更新通常不需要先 down，直接执行 up -d 即可。

至少建立：

- MySQL 定时逻辑备份；
- workspace_data 文件级或卷级备份；
- Chroma 数据快照；
- 备份保留周期；
- 定期恢复演练；
- 备份文件加密和访问控制。

在没有验证恢复流程前，不能仅凭“已经产生备份文件”宣称可恢复。

## 10. 后续版本更新

更新前：

1. 确认新版本的测试和镜像已通过；
2. 记录当前 tag 和镜像版本；
3. 备份数据库和 workspace；
4. 阅读 CHANGELOG；
5. 确认是否有数据库结构变化。

更新示例：

    cd /opt/mini-claude-code

    git fetch --tags
    git checkout v0.1.1

    docker compose -f docker/docker-compose.yml config -q
    docker compose -f docker/docker-compose.yml build
    docker compose -f docker/docker-compose.yml up -d
    docker compose -f docker/docker-compose.yml ps

    curl http://127.0.0.1:3000/api/health

Docker 会复用未变化的镜像层，命名卷不会因为重新构建容器而删除。

## 11. 回滚

如果新版本启动失败：

    cd /opt/mini-claude-code
    git checkout v0.1.0
    docker compose -f docker/docker-compose.yml up -d --build
    docker compose -f docker/docker-compose.yml ps

然后重新验证 health 和关键任务。

当前项目仍使用 SQLAlchemy create_all，没有 Alembic migration。若未来版本修改数据库结构，单纯切换旧代码不一定能安全回滚数据库，因此在生产升级前应先补：

- Alembic 迁移；
- 向前/向后兼容策略；
- 数据库备份；
- 迁移失败恢复演练。

## 12. 监控和日常运维

最小运维检查：

    docker compose -f docker/docker-compose.yml ps
    docker compose -f docker/docker-compose.yml logs --tail=100 api
    curl http://127.0.0.1:3000/api/health

还应关注：

- CPU、内存、磁盘和 inode；
- Docker volume 容量；
- API 5xx 和 SSE 断开；
- MySQL、Redis 健康；
- 模型 API 可达性、限流和余额；
- 平均任务耗时和 token；
- 失败任务、人工介入和安全拦截；
- workspace 和 Trace 增长速度；
- 备份是否按期成功。

## 13. 当前项目的生产限制

本项目已经适合单机作品集演示，但正式生产部署仍需处理：

1. Shell 策略是应用层防护，不是内核级沙箱；
2. JSON Trace 是单机基线，不适合多副本共享；
3. 确认超时调度尚未迁移到分布式任务系统；
4. 数据库没有 Alembic migration；
5. Compose 使用示例数据库密码；
6. 外部模型可能接收代码上下文；
7. code-server 没有包含在 Compose；
8. 尚未配置集中日志、告警、备份和灾难恢复；
9. 真实模型 single/multi-Agent benchmark 仍待授权执行。

对校招作品集而言，应将其表述为“已完成可复现的单机工程化部署基线”，不要表述为已经达到大规模生产级。

## 14. 推荐的两阶段落地路线

### 阶段 A：先完成作品集服务器演示

- 单台 Linux 服务器；
- Git tag 发布；
- Docker Compose；
- 域名和 HTTPS；
- 强随机密钥；
- 仅开放 22、80、443；
- 定期备份 MySQL 和 workspace；
- 演示登录、代码修改、验证、HITL 和 Trace。

### 阶段 B：再补工程化发布

- GitHub Actions 构建并扫描镜像；
- 镜像推送 GHCR 或企业仓库；
- 服务器只执行 pull 和滚动更新；
- Alembic migration；
- rootless 任务容器和资源限制；
- 集中 Trace、日志和告警；
- secret manager；
- 备份恢复演练；
- 多副本部署和负载均衡。

## 15. 部署完成检查清单

- [ ] 服务器运行的是明确 commit/tag；
- [ ] .env 未提交，权限为 600；
- [ ] JWT 和数据库密码已替换；
- [ ] MySQL、Redis、API 未暴露公网；
- [ ] 域名和 HTTPS 正常；
- [ ] 四个容器均为 healthy；
- [ ] /healthz 和 /api/health 通过；
- [ ] workspace、MySQL、Redis、Chroma 使用持久卷；
- [ ] 没有运行 docker compose down -v；
- [ ] 已验证备份与至少一次恢复；
- [ ] SSE 长连接可以完成；
- [ ] 模型 API 可达，且明确代码数据边界；
- [ ] code-server 若启用，已完成认证和 workspace 隔离；
- [ ] 记录本次发布时间、版本、验证结果和回滚版本。

## 16. 与本仓库相关的文件

- [Docker Compose](../docker/docker-compose.yml)
- [API Dockerfile](../docker/Dockerfile)
- [前端 Dockerfile](../docker/frontend.Dockerfile)
- [容器 Nginx 配置](../docker/nginx.conf)
- [环境变量模板](../.env.example)
- [Docker smoke test](../scripts/docker_smoke_test.sh)
- [架构说明](../ARCHITECTURE.md)
- [验收审计](acceptance-audit.md)
