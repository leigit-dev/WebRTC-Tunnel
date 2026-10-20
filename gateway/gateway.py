import asyncio
import json
import struct
import logging
import os
from urllib.parse import urlparse
from aiohttp import web, ClientSession
from aiortc import RTCPeerConnection, RTCSessionDescription

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("gateway")

# 目标内部服务地址
UPSTREAM = os.environ.get("UPSTREAM", "http://127.0.0.1:8080")
# 网关对外提供信令 /offer 的端口
PORT = int(os.environ.get("PORT", "9000"))

# 协议帧类型定义
REQ_HEADERS, REQ_BODY, REQ_END = 1, 2, 3
RES_HEADERS, RES_BODY, RES_END, RES_ERROR = 0x11, 0x12, 0x13, 0x14

# 请求头过滤：不能让 aiohttp 帮我们处理 Host 和 Length
SKIP_REQ = {"host", "content-length", "connection", "transfer-encoding"}
# 响应头过滤：只过滤掉连接级别的头，保留 content-length 和 content-encoding 给浏览器处理
SKIP_RES = {"transfer-encoding", "connection"}

pcs = set()


def filter_headers(h, skip):
    return {k: v for k, v in h.items() if k.lower() not in skip}


async def send_frame(channel, req_id, ftype, payload: bytes):
    # 背压控制：如果缓冲区积压超过 4MB，暂停发送，等待浏览器消费
    while channel.bufferedAmount > 4 * 1024 * 1024:
        await asyncio.sleep(0.01)
    channel.send(struct.pack("!IB", req_id, ftype) + payload)


async def forward_request(channel, session, req_id, meta, body: bytes):
    parsed = urlparse(meta["url"])
    # 拼接上游 URL (保留 query 参数)
    url = UPSTREAM.rstrip("/") + parsed.path + (("?" + parsed.query) if parsed.query else "")
    headers = filter_headers(meta.get("headers", {}), SKIP_REQ)
    
    try:
        # 关键修改：auto_decompress=False 避免 aiohttp 自动解压 gzip
        # 这样浏览器才能收到正确的 content-encoding 和 content-length
        async with session.request(
            meta["method"], url, headers=headers, data=body or None, auto_decompress=False
        ) as resp:
            
            res_meta = {
                "status": resp.status,
                "statusText": resp.reason or "",
                "headers": filter_headers(dict(resp.headers), SKIP_RES),
            }
            
            # 发送响应头
            await send_frame(channel, req_id, RES_HEADERS, json.dumps(res_meta).encode())
            
            # 流式读取响应体并逐块发送
            async for chunk in resp.content.iter_any():
                await send_frame(channel, req_id, RES_BODY, chunk)
                
            # 发送结束帧
            await send_frame(channel, req_id, RES_END, b"")
            
    except Exception as e:
        log.exception("forward failed")
        await send_frame(channel, req_id, RES_ERROR, json.dumps({"message": str(e)}).encode())


async def handle_offer(request):
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
    
    # 这里默认没配 STUN，局域网/localhost 够用。
    # 如果部署公网需要打洞，请在这里添加 iceServers
    pc = RTCPeerConnection()
    pcs.add(pc)
    session = ClientSession()

    @pc.on("datachannel")
    def on_datachannel(channel):
        channel._reqs = {}

        @channel.on("message")
        async def on_message(message):
            if not isinstance(message, (bytes, bytearray)) or len(message) < 5:
                return
            
            # 解析帧头：4字节 RequestID + 1字节 FrameType
            req_id, ftype = struct.unpack("!IB", bytes(message[:5]))
            payload = bytes(message[5:])
            
            if ftype == REQ_HEADERS:
                channel._reqs[req_id] = {"meta": json.loads(payload.decode()), "body": []}
            elif ftype == REQ_BODY and req_id in channel._reqs:
                channel._reqs[req_id]["body"].append(payload)
            elif ftype == REQ_END:
                # 收到完整请求后，丢给后台任务处理
                st = channel._reqs.pop(req_id, None)
                if st:
                    asyncio.create_task(forward_request(
                        channel, session, req_id, st["meta"], b"".join(st["body"])))

    @pc.on("connectionstatechange")
    async def on_state():
        if pc.connectionState in ("failed", "closed"):
            await session.close()
            pcs.discard(pc)

    await pc.setRemoteDescription(offer)
    await pc.setLocalDescription(await pc.createAnswer())

    # 等待 ICE 收集完成，返回非 trickle 的完整 SDP
    if pc.iceGatheringState != "complete":
        fut = asyncio.get_event_loop().create_future()

        @pc.on("icegatheringstatechange")
        def on_ice():
            if pc.iceGatheringState == "complete" and not fut.done():
                fut.set_result(None)

        try:
            await asyncio.wait_for(fut, timeout=5)
        except asyncio.TimeoutError:
            pass

    return web.json_response({
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type,
    })


async def on_shutdown(app):
    for pc in list(pcs):
        await pc.close()
    pcs.clear()


def main():
    app = web.Application()
    # 信令服务器会把浏览器的 offer POST 到这个接口
    app.router.add_post("/offer", handle_offer)
    app.on_shutdown.append(on_shutdown)
    
    log.info(f"Gateway starting on port {PORT}, forwarding to {UPSTREAM}")
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()