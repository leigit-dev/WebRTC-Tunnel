import os
from aiohttp import web, ClientSession
from urllib.parse import quote

BASE = os.path.dirname(os.path.abspath(__file__))
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:9000")
PORT = int(os.environ.get("PORT", "8000"))

async def serve_gate(request):
    # 网关页面现在固定在 /tunnel/gateway
    return web.FileResponse(os.path.join(BASE, "gate.html"))

async def serve_sw(request):
    return web.FileResponse(
        os.path.join(BASE, "sw.js"),
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )

async def handle_signal(request):
    body = await request.read()
    try:
        async with request.app["session"].post(
            GATEWAY_URL.rstrip("/") + "/offer",
            data=body,
            headers={"Content-Type": "application/json"},
        ) as resp:
            data = await resp.read()
            return web.Response(body=data, status=resp.status, content_type="application/json")
    except Exception as e:
        return web.json_response({"error": str(e)}, status=502)

# 🌟 新增：全局兜底拦截
async def catch_all(request):
    # 获取用户原本想访问的完整路径和参数
    target = request.path_qs
    # 重定向到网关页，并将目标路径作为参数带上
    raise web.HTTPFound(f"/tunnel/gateway?redirect={quote(target)}")

async def on_startup(app):
    app["session"] = ClientSession()

async def on_cleanup(app):
    await app["session"].close()

def main():
    app = web.Application()
    # 固定的网关页面路由
    app.router.add_get("/tunnel/gateway", serve_gate)
    # 预留的 RTC 路由
    app.router.add_get("/sw.js", serve_sw)
    app.router.add_post("/signal", handle_signal)
    
    # 🌟 兜底路由：访问其他任何路径都会被重定向
    app.router.add_route("*", "/{tail:.*}", catch_all)
    
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()