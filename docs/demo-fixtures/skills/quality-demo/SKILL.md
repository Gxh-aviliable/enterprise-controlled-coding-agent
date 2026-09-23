---
name: quality-demo
description: |
  演示完整 Skill 包的参考文件读取与受控 Python 脚本执行。
  用户要求验证 Skill 资源可用性时使用。
metadata:
  purpose: acceptance-demo
---

使用 read_skill_resource 读取 references/guide.md。
在获得任务所需权限和审批后，通过平台 bash 工具使用 Python 运行 scripts/check.py。
使用平台提供的沙箱资源目录；不要猜测宿主路径。
报告实际输出和验证状态，不安装依赖，不请求额外权限。
