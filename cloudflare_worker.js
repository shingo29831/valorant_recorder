/**
 * Cloudflare Workers 用のプロキシスクリプト (JWT公開鍵認証版)
 * 
 * デプロイ方法:
 * 1. Cloudflare Workers に新規サービスを作成し、このコードを貼り付ける。
 * 2. Cloudflareのダッシュボードから、以下の環境変数を設定する:
 *    - HENRIK_API_KEY: 実際のHenrik APIキー (必須)
 *    - PUBLIC_KEY: auth.pub の内容 (必須)
 */
import { jwtVerify, importSPKI } from 'jose';

export default {
  async fetch(request, env, ctx) {
    // 1. ボット対策: User-Agentの検証
    const userAgent = request.headers.get("User-Agent") || "";
    const isBot = /bot|crawler|spider|crawling|curl|wget|postman/i.test(userAgent);
    if (!userAgent || isBot || !userAgent.includes("ValorantRecorder")) {
      return new Response("Forbidden", { status: 403 });
    }

    // 2. ボット対策: ブラウザからのアクセス(CORS)をブロック
    if (request.method === "OPTIONS" || request.headers.has("Origin")) {
      return new Response("Forbidden", { status: 403 });
    }

    // 3. 認証: JWTの検証
    const authHeader = request.headers.get("Authorization");
    if (!authHeader || !authHeader.startsWith("Bearer ")) {
      return new Response("Unauthorized: Missing Token", { status: 401 });
    }

    try {
      const token = authHeader.split(" ")[1];
      if (!env.PUBLIC_KEY) {
        return new Response("Internal Server Error: PUBLIC_KEY not configured", { status: 500 });
      }
      const publicKey = await importSPKI(env.PUBLIC_KEY, 'RS256');
      await jwtVerify(token, publicKey);
    } catch (error) {
      return new Response("Unauthorized: Invalid Signature or Token Expired", { status: 401 });
    }

    // 4. Henrik API へのプロキシ
    const url = new URL(request.url);
    const targetUrl = new URL(url.pathname + url.search, "https://api.henrikdev.xyz");
    
    const headers = new Headers(request.headers);
    // クライアントからのJWTトークンを削除し、環境変数のHenrik APIキーに差し替える
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
