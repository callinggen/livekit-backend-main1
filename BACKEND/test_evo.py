import httpx
import asyncio

async def test():
    headers = {"apikey": "MySuperSecretKey123!", "Content-Type": "application/json"}
    
    # 1. Fetch Chats
    url_chats = "http://localhost:8080/chat/findChats/callinggen_default"
    resp = httpx.post(url_chats, headers=headers, json={})
    print("CHATS:", len(resp.json()) if isinstance(resp.json(), list) else resp.json())
    
    # 2. Fetch Messages
    url_msgs = "http://localhost:8080/chat/findMessages/callinggen_default"
    resp = httpx.post(url_msgs, headers=headers, json={"where": {"remoteJid": "917655038727@s.whatsapp.net"}})
    print("MESSAGES (with where):", type(resp.json()), resp.status_code)
    if resp.status_code != 200:
        print("TRYING WITHOUT where:")
        resp = httpx.post(url_msgs, headers=headers, json={"remoteJid": "917655038727@s.whatsapp.net"})
        print("MESSAGES (without where):", type(resp.json()), resp.status_code)

if __name__ == "__main__":
    asyncio.run(test())
