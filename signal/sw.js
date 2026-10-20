let workerPort = null;

self.addEventListener("message", (e) => {
  if (e.data.type === "port") {
    workerPort = e.data.port;
  }
});

self.addEventListener("activate", (e) => {
  // 确保新的 SW 立即接管所有页面
  e.waitUntil(self.clients.claim());
});

// 白名单：这些请求绝对不能被隧道拦截，必须走原生网络
function isBootstrap(pathname) {
  return (
    pathname.startsWith("/tunnel/") || // 网关页面本身
    pathname === "/sw.js" ||           // SW 脚本
    pathname === "/signal"             // 信令交互
  );
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // 1. 跨域请求或非 HTTP(S) 协议，直接放行
  if (url.origin !== self.location.origin) return;

  // 2. 白名单请求，直接放行
  if (isBootstrap(url.pathname)) return;

  // 3. 其余所有同源请求，拦截并走隧道
  event.respondWith(handleFetch(req));
});

function headersToObject(h) {
  const o = {};
  h.forEach((v, k) => (o[k] = v));
  return o;
}

async function handleFetch(request) {
  const url = new URL(request.url);
  
  // 🌟 兜底策略 1：如果 Worker 端口不存在（比如刚启动或网关已关闭）
  if (!workerPort) {
    return handleDisconnected(request);
  }

  const { port1, port2 } = new MessageChannel();
  const reqId = (handleFetch._id = (handleFetch._id || 0) + 1);

  // 包装一个 5 秒超时的 Promise，防止网关被关后请求一直挂起
  const timeoutMs = 5000;
  let timeoutId;

  const headersPromise = new Promise((resolve, reject) => {
    timeoutId = setTimeout(() => {
      reject(new Error("Tunnel Timeout"));
    }, timeoutMs);

    port1.onmessage = (e) => {
      if (e.data.type === "headers") {
        clearTimeout(timeoutId);
        resolve(e.data);
      } else if (e.data.type === "error") {
        clearTimeout(timeoutId);
        reject(new Error(e.data.message));
      }
    };
  });

  try {
    // 读取请求体并发送给 Worker
    const body = request.body ? await request.arrayBuffer() : null;
    const meta = {
      method: request.method,
      url: request.url,
      headers: headersToObject(request.headers),
    };
    
    const transfer = [port2];
    if (body) transfer.push(body);
    
    // 向 Worker 发送请求，转移端口和请求体
    workerPort.postMessage({ type: "request", reqId, meta, body, port: port2 }, transfer);

    // 等待响应头
    const resMeta = await headersPromise;

    // 构造流式响应
    let controller;
    const stream = new ReadableStream({
      start(c) { controller = c; }
    });

    // 响应体超时控制（如果在传大文件时网关被关）
    let bodyTimeoutId;
    const resetBodyTimeout = () => {
      clearTimeout(bodyTimeoutId);
      bodyTimeoutId = setTimeout(() => {
        controller.error(new Error("Body transfer timeout"));
      }, timeoutMs * 2); // 数据传输给稍微长一点的超时，比如 10 秒没动静就断
    };
    
    resetBodyTimeout();

    port1.onmessage = (e) => {
      const d = e.data;
      resetBodyTimeout();
      if (d.type === "chunk") {
        controller.enqueue(new Uint8Array(d.data));
      } else if (d.type === "end") {
        clearTimeout(bodyTimeoutId);
        controller.close();
      } else if (d.type === "error") {
        clearTimeout(bodyTimeoutId);
        controller.error(new Error(d.message));
      }
    };

    return new Response(stream, {
      status: resMeta.status,
      statusText: resMeta.statusText,
      headers: resMeta.headers,
    });

  } catch (err) {
    clearTimeout(timeoutId);
    
    // 如果是网关断开或超时导致的错误
    if (err.message === "Tunnel Timeout" || err.message.includes("closed") || err.name === "NetworkError") {
      return handleDisconnected(request);
    }
    
    // 其他业务错误
    return new Response(err.message, { status: 502 });
  }
}

// 🌟 处理断连逻辑
function handleDisconnected(request) {
  // 如果用户是在刷新页面或跳转页面（导航请求），直接 302 重定向回网关页
  if (request.mode === "navigate") {
    const redirectUrl = `/tunnel/gateway?redirect=${encodeURIComponent(request.url)}`;
    return Response.redirect(redirectUrl, 302);
  }
  
  // 如果是页面里的 fetch/ajax 请求，返回 503 错误，由前端逻辑处理
  return new Response(JSON.stringify({ error: "Tunnel Disconnected" }), {
    status: 503,
    headers: { "Content-Type": "application/json" }
  });
}