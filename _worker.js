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
    const url = new URL(request.url);

    // JWTの取得 (ヘッダー または クエリパラメータ)
    const authHeader = request.headers.get("Authorization");
    let token = null;
    if (authHeader && authHeader.startsWith("Bearer ")) {
      token = authHeader.split(" ")[1];
    } else if (url.searchParams.has("token")) {
      token = url.searchParams.get("token");
    }

    // 認証処理の共通化
    const authenticate = async () => {
      if (!token) throw new Error("Missing Token");
      if (!env.PUBLIC_KEY) throw new Error("PUBLIC_KEY not configured");
      await verifyJwt(token, env.PUBLIC_KEY);
    };

    // 1. バージョン確認API (認証必須)
    if (url.pathname === "/api/version") {
      try {
        await authenticate();
      } catch (e) {
        return new Response("Unauthorized: Invalid or Missing Token", { status: 401 });
      }
      return new Response(JSON.stringify({
        version: env.APP_VERSION || "1.0.0",
        // ダウンロードURLをWorker自身のエンドポイントに向ける
        download_url: `${url.origin}/download/update`
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      });
    }

    // 2. GitHub Private リポジトリからのダウンロードプロキシ (インストーラー & アップデート)
    if (url.pathname === "/download/installer" || url.pathname === "/download/update") {
      try {
        await authenticate();
      } catch (e) {
        return new Response("Unauthorized: Invalid or Missing Token", { status: 401 });
      }

      if (!env.GITHUB_PAT) {
        return new Response("Internal Server Error: GITHUB_PAT not configured", { status: 500 });
      }

      const owner = "shingo29831";
      const repo = "valorant-recorder-release";
      const assetName = url.pathname === "/download/installer" ? "ValoReco_Setup.exe" : "update.zip";

      // GitHub API で最新のリリース情報を取得 (APP_VERSIONに依存しない)
      const releaseUrl = `https://api.github.com/repos/${owner}/${repo}/releases/latest`;
      const releaseRes = await fetch(releaseUrl, {
        headers: {
          "User-Agent": "Cloudflare-Worker",
          "Authorization": `Bearer ${env.GITHUB_PAT}`
        }
      });
      
      if (!releaseRes.ok) {
        const errorText = await releaseRes.text();
        return new Response(`GitHub API Error: ${releaseRes.status} ${releaseRes.statusText}\n${errorText}`, { status: releaseRes.status });
      }
      const releaseData = await releaseRes.json();
      const asset = releaseData.assets.find(a => a.name === assetName);
      if (!asset) return new Response("Asset not found in the release", { status: 404 });

      // アセットのダウンロード用URLを取得 (リダイレクト先を取得)
      const assetRes = await fetch(asset.url, {
        method: "GET",
        headers: {
          "User-Agent": "Cloudflare-Worker",
          "Authorization": `Bearer ${env.GITHUB_PAT}`,
          "Accept": "application/octet-stream"
        },
        redirect: "manual" // リダイレクトを自動で追従しない
      });

      if (assetRes.status === 302 || assetRes.status === 301) {
        // GitHub が返す S3 の一時的な署名付き URL にリダイレクトさせる
        const s3Url = assetRes.headers.get("Location");
        return Response.redirect(s3Url, 302);
      }

      return new Response("Failed to get download URL from GitHub", { status: 500 });
    }

    // 3. ボット対策 & アプリケーション制限 (APIプロキシ用)
    const userAgent = request.headers.get("User-Agent") || "";
    const isBot = /bot|crawler|spider|crawling|curl|wget|postman/i.test(userAgent);
    if (!userAgent || isBot || (!userAgent.includes("ValorantRecorder") && !userAgent.includes("ValoReco"))) {
      return new Response("Forbidden", { status: 403 });
    }

    if (request.method === "OPTIONS" || request.headers.has("Origin")) {
      return new Response("Forbidden", { status: 403 });
    }

    // 4. APIプロキシ用の認証
    try {
      await authenticate();
    } catch (e) {
      return new Response("Unauthorized: Invalid or Missing Token", { status: 401 });
    }

    // 5. Henrik API へのプロキシ
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
