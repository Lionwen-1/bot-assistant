# Docker 项目的 QQ 聊天对接

本机 Docker 新建项目会启动 AstrBot，并为每个 QQ 号创建独立 NapCat 容器。容器处于同一个内部网络，每个 NapCat 已预置自己的反向 WebSocket 地址。

首次聊天对接时，仍需在 AstrBot 控制台为**每个 QQ 号**创建一个 OneBot v11 平台：

1. 在 bot助手的账号卡片菜单中点击“对接 AstrBot”，读取该号的反向 WebSocket 端口。
2. 在 AstrBot 控制台新建 OneBot v11 平台，按卡片提示填写端口；主机填 `0.0.0.0`，令牌留空。
3. 保存后查看 AstrBot 日志，确认该平台适配器已连接。每个账号使用不同端口。

QQ 显示在线只说明 NapCat 登录成功，不能单独证明 AstrBot 已接收消息。若接入已有自建 Compose 项目，bot助手不会改写其 Docker 网络或 NapCat 反向 WebSocket 配置，应按原项目的网络拓扑对接。

AstrBot 官方说明：[OneBot v11 接入文档](https://github.com/AstrBotDevs/AstrBot/wiki/en-platform-aiocqhttp)。
