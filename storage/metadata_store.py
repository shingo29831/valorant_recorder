import json
import os
from datetime import datetime
from core.config import Config

class MetadataStore:
    def __init__(self, config: Config):
        self.config = config

    def save_match_metadata(self, match_data: dict, mmr_change: int) -> str:
        save_dir = self.config.SAVE_DIR
        os.makedirs(save_dir, exist_ok=True)
        
        match_id = match_data['metadata']['matchid']
        
        # 録画開始時刻が存在する場合はファイル名・メタデータの日時として一貫利用
        start_ts = (
            match_data.get("local_match_start_time")
            or (match_data.get("local_recording_start_time_ms", 0) / 1000.0 if match_data.get("local_recording_start_time_ms") else None)
            or match_data.get("metadata", {}).get("game_start")
        )
        if start_ts:
            try:
                timestamp = datetime.fromtimestamp(float(start_ts)).strftime("%Y%m%d_%H%M%S")
            except (ValueError, OSError, OverflowError):
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        filename = f"match_{timestamp}_{match_id}.json"
        filepath = os.path.join(save_dir, filename)

        payload = {
            "match_info": match_data,
            "mmr_change": mmr_change,
            "recorded_at": timestamp
        }

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=4, ensure_ascii=False)
        
        return filepath
