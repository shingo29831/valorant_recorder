import os
import sys
import soundcard as sc
import numpy as np
import traceback

# プロジェクトルートのモジュールをインポートできるようにパスを追加
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from recorder.audio_processor_wrapper import AudioProcessorWrapper

def test_mic(mic_index=None):
    try:
        mics = sc.all_microphones()
        print("--- 利用可能なマイク一覧 ---")
        for i, m in enumerate(mics):
            print(f"{i}: {m.name}")
        
        if mic_index is not None and 0 <= mic_index < len(mics):
            mic = mics[mic_index]
        else:
            mic = sc.default_microphone()
            
        print(f"\nマイク [{mic.name}] で録音テストを開始します...")
        
        sample_rate = 48000
        channels = 2  # テスト用にステレオで取得
        
        print("AudioProcessorWrapper を初期化しています...")
        processor = AudioProcessorWrapper(sample_rate=sample_rate, channels=channels)
        print("DLLのロードおよび初期化に成功しました！\n")

        print("マイクに向かって声を出してください。（約3秒間）")
        
        with mic.recorder(samplerate=sample_rate, channels=channels) as rec:
            for _ in range(50):
                # マイクから音声データを取得
                data = rec.record(numframes=1024)
                
                # 処理前のピーク音量
                peak_raw = np.max(np.abs(data))
                
                # C++ DLLで音声処理 (HPF -> Denoise -> AGC)
                processed_data = processor.process(data)
                
                # 処理後のピーク音量
                peak_processed = np.max(np.abs(processed_data))
                
                print(f"音量ピーク - Raw: {peak_raw:.5f} | Processed: {peak_processed:.5f}")
                
        print("\nテストが正常に完了しました。")
    except Exception as e:
        print(f"エラー発生:\n{traceback.format_exc()}")

if __name__ == "__main__":
    # コマンドライン引数でマイクのインデックスを指定可能にする (例: python test_mic.py 3)
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else None
    test_mic(idx)