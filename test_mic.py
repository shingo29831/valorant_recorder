import soundcard as sc
import numpy as np
import traceback

def test_mic():
    try:
        mics = sc.all_microphones()
        print("--- 利用可能なマイク一覧 ---")
        for i, m in enumerate(mics):
            print(f"{i}: {m.name}")
        
        mic = sc.default_microphone()
        print(f"\nデフォルトマイク [{mic.name}] で録音テストを開始します...")
        print("マイクに向かって声を出してください。")
        
        with mic.recorder(samplerate=48000) as rec:
            for _ in range(50):  # 約2〜3秒間
                data = rec.record(numframes=1024)
                peak = np.max(np.abs(data))
                print(f"音量ピーク: {peak:.5f}")
    except Exception as e:
        print(f"エラー発生:\n{traceback.format_exc()}")

if __name__ == "__main__":
    test_mic()