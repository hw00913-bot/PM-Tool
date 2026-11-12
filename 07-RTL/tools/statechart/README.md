# Statechart 执行器

本目录收录 Lauren Tan 的 Pstack `0.15.36` Statechart 运行文件。源码取自 `08-pstack/skills/statechart/scripts/`，版本与作者来自 `08-pstack/plugin.json`。执行器和原始 [MIT 许可](LICENSE) 随 RTL 提供。

RTL 在 `run.ts` 中将 TLC 定位改为读取本机配置。把 `TLA2TOOLS` 设为已有 `tla2tools.jar` 的绝对路径。Java 11 或更高版本可从 `PATH` 读取，也可用 `JAVA` 指定本机 Java。上游固定的版本是 TLC `1.7.4`，JAR 的 SHA-256 为 `936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88`。

`statechart` 启动脚本支持 Node.js 22.18 至 22.x、Node.js 23.6 及以上、Bun 1.1.5 及以上，或 Deno 2.0 及以上。运行只使用运行时内置模块，`package.json` 保留 ESM 声明。执行命令和报告用法见 [RTL Statechart Skill](../../skills/statechart/SKILL.md)。
