# 演示项目：联网执行与临时装包

2026-09-15按用户要求简化：执行容器默认可以直接联网，安装依赖沿用现有Shell流程，不新增包源白名单、下载服务或装包专用审批。此变更取代9月14日交付时“普通执行禁网”的默认行为，历史报告仍保留其当时结果。

## 直接使用

Python：`python -m pip install requests`，下一条命令即可在项目文件中 `import requests`。已配置user安装目录，无需sudo、虚拟环境或修改基础镜像。

Node：在有`package.json`的目录执行`npm install`，随后照常执行`npm test`或`npm run build`。支持工作区根目录和已有的嵌套前端项目。新建项目时先用一条工具调用生成`package.json`，下一次调用再装包，让执行器识别并挂载对应的`node_modules`。

每条Shell仍使用独立容器。Python包、npm缓存及各项目的`node_modules`存放在宿主暂存根下的`dependencies/<workspace摘要>/`，只挂载当前工作区对应目录，跨命令及API重启可复用。npm正常生成的`.bin`链接保留在依赖挂载内，不作为普通源码回写。包管理器对`package.json`、锁文件和源码的修改仍正常回写并显示Diff。

依赖属于工作区，不是系统全局，也不会在任务结束时立即删除。可使用`pip uninstall`、`npm uninstall`管理；暂未增加自动过期/共享缓存/后台清理服务。修改依赖会使旧验证失效；收据记录`dependencies_changed`，验证版本包含已安装依赖的元数据摘要。文件恢复不会撤销已安装包或网络操作。

## 配置与边界

默认`SANDBOX_NETWORK_ENABLED=true`，Docker使用bridge网络；需要复验离线模式时设为false即可。基础镜像仍需有Python或Node，包通过pip/npm在非root可写目录安装。预检只对基础运行时明确缺失提前失败，不再因基础镜像未预装可安装的Python模块而拒绝运行。

保留容器隔离、只读系统根、CPU/内存/PID限制、超时、Stop和原有Shell权限；`/tmp`允许运行安装/构建辅助程序。没有开放宿主Shell、Docker socket、其他用户目录，也未改变远程服务。

## 验证

真实Docker测试：`tests/sandbox/test_docker.py::test_online_pip_npm_install_persist_between_containers`。测试使用独立合成工作区，从PyPI下载`cowsay==6.1`、从npm下载`semver@7.7.2`，在后续容器验证Python导入、嵌套Node项目导入和npm可执行链接；检查跨用户不共享包，安装依赖后旧验证失效，重新测试恢复有效。

测试需显式设置`RUN_DOCKER_SANDBOX_TESTS=1`、`RUN_SANDBOX_NETWORK_TESTS=1`及本机Docker socket。原离线Docker回归显式设置network=false，继续检查原隔离/取消/恢复边界。实际结果见当天开发日志及`docs/release-evidence/network-*-20260915.xml`。
