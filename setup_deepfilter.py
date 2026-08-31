import os
import shutil
from pathlib import Path

def main():
    base_dir = Path(__file__).parent.absolute()
    
    # 環境変数からDeepFilterNetのパスを取得、なければ相対パスを推測
    df_src_env = os.environ.get("DEEPFILTER_SRC")
    if df_src_env:
        src_dir = Path(df_src_env)
    else:
        # デフォルト: valorant_recorder と同じ階層にある DeepFilterNet フォルダ
        src_dir = base_dir.parent / "DeepFilterNet"
        # フォールバック: ハードコードされたパス (互換性のため)
        if not src_dir.exists():
            fallback_dir = Path(r"C:\Users\Shingo\Projects\DeepFilterNet")
            if fallback_dir.exists():
                src_dir = fallback_dir

    if not src_dir.exists():
        print(f"❌ エラー: DeepFilterNetのソースディレクトリが見つかりません: {src_dir}")
        print("環境変数 DEEPFILTER_SRC を設定するか、正しい場所に配置してください。")
        return

    dest_dir = base_dir / "cpp" / "audio_processor"

    # 必要なディレクトリの作成
    (dest_dir / "bin").mkdir(parents=True, exist_ok=True)
    (dest_dir / "lib").mkdir(parents=True, exist_ok=True)
    (dest_dir / "include").mkdir(parents=True, exist_ok=True)

    print("=== 1. ヘッダーの検索とコピー ===")
    header_found = False
    for root, _, files in os.walk(src_dir / "target"):
        if "deep_filter.h" in files:
            header_src = Path(root) / "deep_filter.h"
            shutil.copy2(header_src, dest_dir / "include" / "deep_filter.h")
            print(f"✅ ヘッダーをコピーしました: {header_src}")
            header_found = True
            break
    if not header_found:
        print("❌ 警告: ヘッダーが見つかりません。")

    print("\n=== 2. DLLとLIBの検索とコピー ===")
    target_dir = src_dir / "target"
    dll_found = False
    lib_found = False

    for root, dirs, files in os.walk(target_dir):
        # deps(中間ファイル)やbuildディレクトリはスキップ
        if "deps" in Path(root).parts or "build" in Path(root).parts:
            continue
        
        for file in files:
            file_lower = file.lower()
            if "deep_filter" in file_lower or "deepfilter" in file_lower:
                file_path = Path(root) / file
                if file_lower.endswith(".dll"):
                    shutil.copy2(file_path, dest_dir / "bin" / "deep_filter.dll")
                    print(f"✅ DLLをコピーしました: {file_path}")
                    dll_found = True
                elif file_lower.endswith(".lib") or file_lower.endswith(".dll.lib"):
                    shutil.copy2(file_path, dest_dir / "lib" / "deep_filter.lib")
                    print(f"✅ LIBをコピーしました: {file_path}")
                    lib_found = True

    if not dll_found:
        print("❌ 警告: DLLが見つかりません。")
    if not lib_found:
        print("❌ 警告: LIBが見つかりません。")

    print("\n=== 3. モデルのダウンロード ===")
    import urllib.request
    
    # Rust (C API) は .tar.gz アーカイブファイルが含まれるディレクトリを読み込む
    model_url = "https://github.com/Rikorose/DeepFilterNet/raw/main/models/DeepFilterNet3_onnx.tar.gz"
    model_dir = dest_dir / "models" / "DeepFilterNet3"
    model_dir.mkdir(parents=True, exist_ok=True)
    model_tar_path = model_dir / "DeepFilterNet3_onnx.tar.gz"

    if not model_tar_path.exists() or model_tar_path.stat().st_size < 1024:
        print(f"モデルをダウンロードしています: {model_url}")
        try:
            urllib.request.urlretrieve(model_url, model_tar_path)
            print(f"✅ モデルのダウンロードが完了しました: {model_tar_path}")
        except Exception as e:
            print(f"❌ ダウンロードに失敗しました: {e}")
    else:
        print(f"✅ モデルアーカイブは既に存在します: {model_tar_path}")

    print("\n🎉 すべての処理が完了しました！")

if __name__ == "__main__":
    main()