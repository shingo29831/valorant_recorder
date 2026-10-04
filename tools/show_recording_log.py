"""Module: @role: 録画カクつき・エラーの診断ログを解析し、根本原因を特定するツール"""
import os
import re
import glob

def analyze_ffmpeg_log(log_path: str):
    if not os.path.exists(log_path):
        print(f"[!] ログファイルが見つかりません: {log_path}")
        return

    print(f"\n==========================================")
    print(f"  FFmpeg 録画診断レポート: {os.path.basename(log_path)}")
    print(f"==========================================\n")

    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    # 1. ddagrab 初期化情報の抽出
    ddagrab_lines = [l.strip() for l in lines if "ddagrab" in l.lower() or "d3d11" in l.lower()]
    print("[1] キャプチャデバイス状態 (ddagrab / D3D11):")
    if ddagrab_lines:
        for l in ddagrab_lines[-5:]:
            print(f"    {l}")
    else:
        print("    (ddagrab情報なし)")

    # 2. 最終統計行 (frame=... fps=... speed=...) の抽出
    stat_pattern = re.compile(r"frame=\s*(\d+)\s+fps=\s*([\d\.]+).*?speed=\s*([\d\.]+)x")
    drop_pattern = re.compile(r"drop=\s*(\d+)")
    last_stats = None
    all_drops = []
    all_speeds = []

    for l in lines:
        m = stat_pattern.search(l)
        if m:
            last_stats = m
            try:
                all_speeds.append(float(m.group(3)))
            except ValueError:
                pass
            dm = drop_pattern.search(l)
            if dm:
                all_drops.append(int(dm.group(1)))

    print("\n[2] キャプチャ & エンコード速度解析:")
    if last_stats:
        frames = last_stats.group(1)
        fps = last_stats.group(2)
        speed = last_stats.group(3)
        avg_speed = sum(all_speeds) / len(all_speeds) if all_speeds else 0.0
        max_drop = max(all_drops) if all_drops else 0

        print(f"    - 最終記録フレーム数 : {frames}")
        print(f"    - 実効処理FPS        : {fps} fps")
        print(f"    - 累積ドロップ数    : {max_drop} フレーム")
        print(f"    - 処理速度倍率(speed): {speed}x (平均: {avg_speed:.2f}x)")

        print("\n[3] 根本原因の診断結果:")
        if float(speed) < 0.98 or avg_speed < 0.98:
            print("    【原因 A: エンコード/パイプ処理の追いつき不足】")
            print("    -> 処理倍率が 1.0x を下回っています。GPUのエンコーダ負荷が高いか、")
            print("       または解像度/プリセットに対してハードウェア性能が不足しています。")
        elif max_drop > 30:
            print("    【原因 B: ddagrab (Desktop Duplication) 画面取得のコマ落ち】")
            print("    -> Windows側で画面更新の取得がブロックされています。")
            print("       ゲーム側の設定（フルスクリーン vs 仮想フルスクリーン）や")
            print("       G-Sync/可変リフレッシュレートとの競合が疑われます。")
        else:
            print("    【正常範囲 / 再生側要因】")
            print("    -> FFmpeg側ではドロップ・遅延は検出されていません。")
            print("       プレイヤー側の再生支援(デコード負荷)またはレンダラー設定を確認してください。")
    else:
        print("    統計行が見つかりませんでした。末尾のログを出力します:")
        for l in lines[-10:]:
            print(f"    {l.strip()}")

    print("\n==========================================\n")

def main():
    import sys
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from datetime import datetime
    save_dir = ""
    try:
        from core.config import Config
        cfg = Config()
        save_dir = getattr(cfg, 'SAVE_DIR', '')
    except Exception:
        pass

    # 確実に実際の保存先フォルダから最新ログを探索
    search_dirs = [d for d in [
        save_dir,
        os.path.join(os.environ.get('USERPROFILE', ''), 'Videos', 'valorant_records'),
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'ValoReco'),
        os.getcwd()
    ] if d]
    
    found_logs = set()
    for d in search_dirs:
        if os.path.exists(d):
            direct = os.path.join(d, "native_ffmpeg.log")
            if os.path.exists(direct):
                found_logs.add(direct)
            for p in glob.glob(os.path.join(d, "**", "native_ffmpeg.log"), recursive=True):
                if os.path.exists(p):
                    found_logs.add(p)

    if found_logs:
        # 更新日時が最も新しいログを確実に解析
        latest_log = max(list(found_logs), key=os.path.getmtime)
        mtime_str = datetime.fromtimestamp(os.path.getmtime(latest_log)).strftime("%Y-%m-%d %H:%M:%S")
        print(f"[*] 検出された最新ログ: {latest_log} (最終更新: {mtime_str})")
        analyze_ffmpeg_log(latest_log)
    else:
        print("[!] native_ffmpeg.log が見つかりませんでした。一度録画を実行してください。")

if __name__ == "__main__":
    main()
