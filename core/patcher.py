import os
import sys
import glob
import importlib.util

def patch_soundcard_lib():
    """
    soundcardライブラリがWAVE_FORMAT_PCMやWAVE_FORMAT_IEEE_FLOATなどの
    非拡張フォーマットのデバイスを読み込もうとした際にクラッシュするバグを修正するパッチ。
    """
    try:
        # soundcardを直接importするとCOMの初期化が走り、PyQt6のCOM初期化(STA)と競合するため、
        # importlibを使ってファイルパスのみを取得する
        spec = importlib.util.find_spec("soundcard")
        if spec is None or spec.origin is None:
            print("[Patcher] Error: soundcard module not found.")
            return
            
        mf_path = os.path.join(os.path.dirname(spec.origin), 'mediafoundation.py')
        if not os.path.exists(mf_path):
            print(f"[Patcher] Error: {mf_path} does not exist.")
            return

        with open(mf_path, 'r', encoding='utf-8') as f:
            content = f.read()

        targets = [
            "assert ppMixFormat[0][0].Format.wFormatTag == 0xFFFE",
            "assert ppMixFormat[0][0].Format.cbSize == 22",
            "assert ppMixFormat[0][0].SubFormat.Data1 == 0x100000",
            "assert ppMixFormat[0][0].SubFormat.Data2 == 0x0080",
            "assert ppMixFormat[0][0].SubFormat.Data3 == 0xaa00",
            "assert [int(x) for x in ppMixFormat[0][0].SubFormat.Data4[0:4]] == [0, 56, 155, 113]"
        ]

        new_content = content
        patched = False
        for target in targets:
            if target in new_content:
                new_content = new_content.replace(target, f"pass  # {target} (patched)")
                patched = True

        # S_FALSE (1) をエラーとして扱わないようにするパッチ (COM初期化の重複時対策)
        s_false_target = "if hresult == S_OK:"
        s_false_patch = "if hresult == S_OK or hresult == 1:  # 1 is S_FALSE"
        if s_false_target in new_content and s_false_patch not in new_content:
            new_content = new_content.replace(s_false_target, s_false_patch)
            patched = True

        # RPC_E_CHANGED_MODE の判定を安全にするパッチ (符号付き/符号なし両対応)
        rpc_target = "if hr + 2 ** 32 == RPC_E_CHANGED_MODE:"
        rpc_patch = "if hr + 2 ** 32 == RPC_E_CHANGED_MODE or hr == RPC_E_CHANGED_MODE:"
        if rpc_target in new_content and rpc_patch not in new_content:
            new_content = new_content.replace(rpc_target, rpc_patch)
            patched = True

        if patched:
            with open(mf_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print("[Patcher] Successfully patched soundcard library (disabled assertions and fixed COM init).")
            
            # キャッシュを削除して確実なリロードを強制
            pycache_dir = os.path.join(os.path.dirname(mf_path), '__pycache__')
            if os.path.exists(pycache_dir):
                for pyc_file in glob.glob(os.path.join(pycache_dir, 'mediafoundation.*.pyc')):
                    try:
                        os.remove(pyc_file)
                    except Exception:
                        pass
        else:
            print("[Patcher] No assertions found to patch or already patched.")
            
    except Exception as e:
        print(f"[Patcher] Failed to patch soundcard library: {e}")
