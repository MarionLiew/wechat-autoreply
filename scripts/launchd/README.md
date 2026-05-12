# launchd 自启 / 自愈

让 daemon 在 macOS 登录后自动启动，崩溃自动重启（最多 30s 后），手动 stop 后保持 stop。

## 安装

```bash
# 1) 拷贝 plist 到 LaunchAgents
cp scripts/launchd/com.marionliew.wechat-autoreply.plist ~/Library/LaunchAgents/

# 2) 加载
launchctl load -w ~/Library/LaunchAgents/com.marionliew.wechat-autoreply.plist

# 3) 立刻启动
launchctl start com.marionliew.wechat-autoreply
```

## 状态 / 控制

```bash
# 看是否运行 + 最后退出码
launchctl list | grep wechat-autoreply

# 重启
launchctl kickstart -k gui/$UID/com.marionliew.wechat-autoreply

# 停止（再开机也不会启动）
launchctl unload -w ~/Library/LaunchAgents/com.marionliew.wechat-autoreply.plist
```

## 日志

- `daemon.log`：Python 内部 RotatingFileHandler，20MB×5 自动轮转。
- `daemon.stdout.log` / `daemon.stderr.log`：launchd 捕获的 stdout/stderr（一般只有 Python 启动期 traceback 落进来）。
- `storage/heartbeat.txt`：每个 tick 写入 ISO 时间戳，外部健康检查看 mtime。

## 健康检查

```bash
# 心跳超过 60s 没更新 = daemon 卡死或挂了
[ $(($(date +%s) - $(stat -f %m storage/heartbeat.txt))) -gt 60 ] && echo "DEAD"
```
