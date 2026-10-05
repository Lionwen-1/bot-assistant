# 接管现有 NapCat 账号

已有独立 NapCat 容器时，服务器管理员可通过 `manager.py adopt` 将其加入 bot助手，无需重建容器或重新扫码。接管前请备份原 Compose 和 NapCat 配置。

通过标准输入提供：

| 字段 | 含义 |
| --- | --- |
| `qq`、`name` | QQ 号码与界面昵称。 |
| `container`、`port` | 现有 NapCat 容器名及本机 OneBot HTTP 端口。 |
| `config_path` | 对应 `onebot11_<QQ>.json` 的服务器绝对路径。 |
| `ws_container`、`ws_port` | 可选；聊天服务的容器名及反向 WebSocket 监听端口，用于检查聊天连接。 |

示例：在**服务器**上执行，替换号码、容器名、端口和路径。

```bash
printf '%s\n' '{"qq":"123456789","name":"已有账号","container":"napcat-existing","port":6700,"config_path":"/srv/bot/config/onebot11_123456789.json","ws_container":"astrbot","ws_port":6199}' | python3 ~/.local/share/bot-assistant/manager.py adopt
```

管理脚本只在服务器读取接口令牌，并验证容器运行且账号在线；令牌不会返回桌面客户端。接管后原 Compose 项目继续维护容器配置，bot助手负责状态、单号登录恢复和昵称。

**移除已接管账号只会从 bot助手的清单中取消接管，原容器继续运行。** 若要重新显示，应再次执行 `adopt`。不要点击“添加账号”另建同号容器；新容器不会自动继承原有 AstrBot WebSocket、归档或告警连接。
