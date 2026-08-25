/**
 * Cloudflare Pages 用プロキシスクリプト (JWT公開鍵認証版)
 * 外部ライブラリ不要 (Web Crypto API使用)
 */

// Base64URLをUint8Arrayに変換
function base64UrlToArrayBuffer(base64Url) {
  const padding = '='.repeat((4 - base64Url.length % 4) % 4);
  const base64 = (base64Url + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

// PEM形式の公開鍵をインポート
async function importPublicKey(pem) {
  const pemHeader = "-----BEGIN PUBLIC KEY-----";
  const pemFooter = "-----END PUBLIC KEY-----";
  const pemContents = pem.replace(pemHeader, '').replace(pemFooter, '').replace(/\s/g, '');
  const binaryDer = base64UrlToArrayBuffer(pemContents);

  return await crypto.subtle.importKey(
    "spki",
    binaryDer,
    {
      name: "RSASSA-PKCS1-v1_5",
      hash: "SHA-256",
    },
    true,
    ["verify"]
  );
}

// JWTの検証
async function verifyJwt(token, pemPublicKey) {
  const parts = token.split('.');
  if (parts.length !== 3) throw new Error('Invalid token format');

  // ペイロードの有効期限(exp)を確認
  const payload = JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/')));
  if (payload.exp && payload.exp < Math.floor(Date.now() / 1000)) {
    throw new Error('Token expired');
  }

  const key = await importPublicKey(pemPublicKey);
  const signature = base64UrlToArrayBuffer(parts[2]);
  const data = new TextEncoder().encode(parts[0] + '.' + parts[1]);

  const isValid = await crypto.subtle.verify(
    "RSASSA-PKCS1-v1_5",
    key,
    signature,
    data
  );

  if (!isValid) throw new Error('Invalid signature');
  return payload;
}

export default {
  async fetch(request, env, ctx) {
    // 1. ボット対策: User-Agentの検証
    const userAgent = request.headers.get("User-Agent") || "";
    const isBot = /bot|crawler|spider|crawling|curl|wget|postman/i.test(userAgent);
    if (!userAgent || isBot || !userAgent.includes("ValorantRecorder")) {
      return new Response("Forbidden", { status: 403 });
    }

    if (request.method === "OPTIONS" || request.headers.has("Origin")) {
      return new Response("Forbidden", { status: 403 });
    }

    // 2. 認証: JWTの検証
    const authHeader = request.headers.get("Authorization");
    if (!authHeader || !authHeader.startsWith("Bearer ")) {
      return new Response("Unauthorized: Missing Token", { status: 401 });
    }

    try {
      const token = authHeader.split(" ")[1];
      if (!env.PUBLIC_KEY) {
        return new Response("Internal Server Error: PUBLIC_KEY not configured", { status: 500 });
      }
      await verifyJwt(token, env.PUBLIC_KEY);
    } catch (error) {
      return new Response("Unauthorized: Invalid Signature or Token Expired", { status: 401 });
    }

    // 3. Henrik API へのプロキシ
    const url = new URL(request.url);
    const targetUrl = new URL(url.pathname + url.search, "https://api.henrikdev.xyz");
    
    const headers = new Headers(request.headers);
    headers.delete("Authorization");
    if (env.HENRIK_API_KEY) {
      headers.set("Authorization", env.HENRIK_API_KEY);
    }
    
    const modifiedRequest = new Request(targetUrl, {
      method: request.method,
      headers: headers,
      body: request.body,
      redirect: "follow"
    });
    
    try {
      const response = await fetch(modifiedRequest);
      return new Response(response.body, response);
    } catch (error) {
      return new Response(JSON.stringify({ error: "Proxy Error" }), {
        status: 500,
        headers: { "Content-Type": "application/json" }
      });
    }
  }
};
