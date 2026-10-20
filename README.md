# WebRTC-Tunnel

基于 WebRTC DataChannel 和 Service Worker 的浏览器端 HTTP 反向代理。无需安装客户端，通过浏览器即可访问内网 HTTP 服务。

在使用Tailscale的过程中，访问内部应用主要有两种方法：

- 安装客户端->登录到Tailnet->访问，在分享给他人或在机房等临时设备登录时较为麻烦

- 使用Funnel，但是速度和流量限制是两大痛点

本项目通过直接在浏览器端构建隧道实现内网穿透访问，兼具了内网穿透和无客户端的优点

如果使用自托管服务作为信令服务器（signaling.py），可以实现完全无公网暴露的内部访问

## 架构

```text
浏览器 (Client)
  ├── Service Worker (拦截同源请求)
  └── WebRTC DataChannel
          │
          ▼ (P2P / UDP)
    信令服务器 (signaling.py) ── 转发 SDP ──▶ 内网网关 (gateway.py)
                                                │
                                                ▼ (HTTP)
                                            你的服务器
```

## 目录结构

```text
rtc/
├── gateway/
│   └── gateway.py       # 内网网关：处理 WebRTC 数据流并转发至内网服务
└── signal/
    ├── signaling.py          # 公网信令服务器：提供页面、SDP 转发、路由兜底
    ├── gate.html        # 控制台页面：管理连接、延迟探测、跳转
    └── sw.js            # Service Worker：流量拦截与超时/断连处理
```

## 启动步骤

**1. 安装依赖**
```bash
pip install aiohttp aiortc
```

**2. 启动内网目标服务**
假设你的内网应用运行在 `8080` 端口。
```bash
# 示例：启动一个简单的 HTTP 服务
python -m http.server 8080
```

**3. 启动内网网关**
```bash
cd gateway
# Windows CMD:
set UPSTREAM=http://127.0.0.1:8080 && python gateway.py
# Windows PowerShell:
$env:UPSTREAM="http://127.0.0.1:8080"; python gateway.py
```

**4. 启动公网信令服务器**
```bash
cd signal
# Windows CMD:
set GATEWAY_URL=http://127.0.0.1:9000 && python signaling.py
# Windows PowerShell:
$env:GATEWAY_URL="http://127.0.0.1:9000"; python signaling.py
```

**5. 访问**
在浏览器（推荐 Edge/Chrome）打开：
`http://localhost:8000/`
会自动重定向至 `/tunnel/gateway`，建立隧道后在新标签页打开目标服务。
对于一般的网站，使用上唯一的差别是需要在后台常驻网关界面以维持连接，对后端没用侵入性。

## 注意事项

1. **HTTPS 限制**：WebRTC 强制要求安全上下文。本地测试需使用 `localhost` 或 `127.0.0.1`；公网部署必须配置 HTTPS 证书。
2. **页面存活**：网关页面（`/tunnel/gateway`）是隧道的维持端点，关闭该标签页会导致隧道断开，Service Worker 会拦截后续请求并重定向回网关页。
3. **Service Worker 缓存**：在开发中，修改 `sw.js` 后可能无法立刻生效，按 F12 -> Application -> Service Workers，点击 `Unregister` 注销，并强制刷新页面。

## 已知限制

- **不支持 WebSocket**：Service Worker 无法拦截 WebSocket 握手，`ws://` 请求会直接失败或绕过隧道。
- **跨域请求**：Service Worker 仅拦截同源请求，页面中引用的第三方 CDN 资源不会通过隧道。
- **Firefox 和其他内核浏览器的兼容性（未测试）**：Firefox 的 Service Worker 不拦截 `XMLHttpRequest`，若主应用依赖 XHR，需在 Chromium 内核下运行；在其他内核浏览器中未进行测试和研究，可能存在`Service Woker`等关键模块的兼容性问题。
- **单进程性能**：`aiortc` 为单线程 CPU 密集型处理，单进程吞吐存在上限。高并发场景需启动多个网关进程并配合 UDP 负载均衡，或者可以换用其他语言实现RTC网关层。
- **全量缓冲**：目前未实现流式上传，极大文件上传可能导致浏览器内存溢出。

## 许可证

MIT