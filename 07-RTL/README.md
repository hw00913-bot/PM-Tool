# RTL 多 Agent 原型 Loop

将已确认的业务场景分派到最多两个隔离工作区，按契约协作，验证精确合入结果，交付可直接双击 `index.html` 的本地页面原型。

- [功能地图](docs/feature-map.md) — 从功能找到负责模块、Skill 和验证用例
- [运行手册](docs/runbook.md) — 开启、派发、接管、验证和交付
- [设计](docs/design.md) — 角色、状态和证据边界
- [Skill 与登记清单](skills/README.md)

本项目保存 Loop 工具。每个原型项目与 Loop 的持久数据目录均独立设置。工具使用 Python 3.11+ 和 Git。浏览器验证另用 Node、Playwright 和验证机上可启动的浏览器，交付页面随包提供资源与 Mock。

## 新项目底座

新项目默认采用 **React + Umi + Ant Design**，模板在 [templates/react](templates/react/README.md)。场景 Agent 提交源码，RTL 构建并核验可双击的 `prototype/index.html`。验收通过后，交付回执提供源码目录，包含依赖锁文件、启动命令和构建配置，供 Coding Agent 接手。原生项目可用 `init --frontend static`。 [React 运行步骤](docs/runbook.md#react-构建和交接)

## 启动口令

> 使用 rtl 开始规划项目

在 RTL 项目上下文中直接发送这句，Master 进入 S0 项目规划。可随请求附上项目材料和原型落盘路径。Master 确认**项目目标、独立原型项目绝对路径、最终入口和已有内容的处置**，再按门禁推进。Master 派发场景 Agent，RTL 命令记录实际启动、候选和证据。使用 Codex App Server 执行时，可按原尝试核对和接管派发。 [Codex 执行手册](docs/runbook.md#通过-codex-执行场景)

接手已有项目时，先用 `runs` 和 `status` 定位当前轮次，按本轮冻结基线和已激活契约推进。历史交付与旧版本的查阅和恢复见 [运行手册](docs/runbook.md#现行依据与历史记录)。

在本目录查看入口及运行不依赖浏览器的测试：

```sh
python3 -m loop --help
python3 -m unittest discover -s tests -v
```

浏览器验证使用 [运行手册](docs/runbook.md#浏览器验证合入和交付) 的环境配置。

```sh
RTL_TEST_BROWSER=1 python3 -m unittest discover -s tests -v
RTL_TEST_REACT_BROWSER=1 python3 -m unittest discover -s tests -p test_react_cli.py -v
```
