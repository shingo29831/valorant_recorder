/**
 * Cloudflare Workers / Pages Functions 用のプロキシスクリプト (Riotアカウント認証版)
 * 
 * デプロイ方法:
 * 1. Cloudflare Workers に新規サービスを作成し、このコードを貼り付ける。
 * 2. Cloudflareのダッシュボードから、以下の環境変数を設定する:
 *    - HENRIK_API_KEY: 実際のHenrik APIキー (必須)
 * 
 * 運用方法:
 * クライアントアプリはローカルのValorantから取得したRiot Access Tokenを
 * Authorizationヘッダーに付与して送信します。
 * このWorkerはRiot公式サーバーでトークンを検証し、正当なユーザーのみを許可します。
 */
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

    // 3. 認証: Riot Access Token の検証
    const authHeader = request.headers.get("Authorization");
    if (!authHeader || !authHeader.startsWith("Bearer ")) {
      return new Response("Unauthorized: Missing Riot Access Token", { status: 401 });
    }

    try {
      // Riot公式のuserinfoエンドポイントでトークンを検証
      const riotVerifyReq = new Request("https://auth.riotgames.com/userinfo", {
        method: "GET",
        headers: {
          "Authorization": authHeader
        }
      });
      const riotRes = await fetch(riotVerifyReq);
      if (!riotRes.ok) {
        return new Response("Unauthorized: Invalid Riot Access Token", { status: 401 });
      }
    } catch (error) {
      return new Response("Internal Server Error during Riot authentication", { status: 500 });
    }

    // 4. Henrik API へのプロキシ
    const url = new URL(request.url);
    const targetUrl = new URL(url.pathname + url.search, "https://api.henrikdev.xyz");
    
    const headers = new Headers(request.headers);
    // クライアントからのRiotトークンを削除し、環境変数のHenrik APIキーに差し替える
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
