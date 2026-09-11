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